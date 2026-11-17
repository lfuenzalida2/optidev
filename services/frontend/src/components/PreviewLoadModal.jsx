import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Alert, App, Button, Modal, Select, Space, Steps, Typography } from 'antd';
import { CodeOutlined, CloudUploadOutlined, ExportOutlined } from '@ant-design/icons';

import { previewsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { useTerminals } from '../contexts/TerminalContext';
import VmDoctor from './VmDoctor';

const STEPS = [
  { key: 'fetch', title: 'Descargar' },
  { key: 'send', title: 'Enviar a la VM' },
  { key: 'checkout', title: 'Checkout' },
  { key: 'env', title: 'Configurar' },
  { key: 'recipe', title: 'Receta' },
];
const POLL_MS = 1000;

// Carga la rama de un PR en una VM de pruebas del usuario y muestra el avance.
// `target` = { repo, branch, card, pull, loadId? } (null = cerrado). Con
// `loadId` abre una carga ya hecha (o en curso) para ver su log.
export default function PreviewLoadModal({ target, vms, onClose, onFinished }) {
  const { message } = App.useApp();
  const { openTerminal } = useTerminals();
  const [vmId, setVmId] = useState(null);
  const [load, setLoad] = useState(null);
  const [starting, setStarting] = useState(false);
  const logRef = useRef(null);

  // Solo al abrir otro PR: si dependiera de `vms`, recargar la lista de VMs al
  // terminar la carga borraba el log que se estaba mirando.
  useEffect(() => {
    setLoad(null);
    if (!target?.loadId) return;
    previewsApi.get(target.loadId).then(setLoad).catch((err) => message.error(extractErrorMessage(err)));
  }, [target, message]);

  useEffect(() => {
    setVmId((current) => (vms.some((v) => v.id === current) ? current : vms[0]?.id ?? null));
  }, [vms]);

  // Mientras corre, se consulta el estado cada segundo.
  useEffect(() => {
    if (!load || load.status !== 'running') return undefined;
    const timer = setTimeout(async () => {
      try {
        const next = await previewsApi.get(load.id);
        setLoad(next);
        if (next.status !== 'running') onFinished();
      } catch (err) {
        message.error(extractErrorMessage(err));
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [load, message, onFinished]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [load?.log]);

  const start = async () => {
    setStarting(true);
    try {
      setLoad(await previewsApi.start({
        repository: target.repo.key, branch: target.branch, vmId,
        cardId: target.card.id, prUrl: target.pull.url,
      }));
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setStarting(false);
    }
  };

  const vm = vms.find((v) => v.id === (load?.vmId ?? vmId));
  // Al terminar bien el paso es "done", que no está en STEPS: todos completos.
  const stepIndex = !load ? -1
    : load.status === 'success' ? STEPS.length : Math.max(0, STEPS.findIndex((s) => s.key === load.step));
  const stepStatus = load?.status === 'failed' ? 'error' : load?.status === 'success' ? 'finish' : 'process';

  return (
    <Modal open={Boolean(target)} width={820} onCancel={onClose} destroyOnHidden
      title={target && <>Cargar <Typography.Text code>{target.branch}</Typography.Text> de {target.repo.key}</>}
      footer={(
        <Space>
          {load?.status === 'success' && load.url && (
            <Button icon={<ExportOutlined />} href={load.url} target="_blank" rel="noreferrer">Abrir</Button>
          )}
          {load && vm && (
            <Button icon={<CodeOutlined />} onClick={() => { openTerminal(vm); onClose(); }}>Terminal</Button>
          )}
          {!load && (
            <Button type="primary" icon={<CloudUploadOutlined />} loading={starting} disabled={!vmId} onClick={start}>
              Cargar
            </Button>
          )}
          {load?.status === 'failed' && <Button onClick={start} loading={starting}>Reintentar</Button>}
          <Button onClick={onClose}>{load?.status === 'running' ? 'Seguir en segundo plano' : 'Cerrar'}</Button>
        </Space>
      )}>
      {target?.loadId && !load ? (
        <Typography.Text type="secondary">Cargando el detalle…</Typography.Text>
      ) : !vms.length && !load ? (
        <Alert type="info" showIcon message="Todavía no cargaste tu VM de pruebas"
          description={<>Agregala en <Link to="/account?seccion=vm" onClick={onClose}>Mi perfil</Link>.</>} />
      ) : !load ? (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Typography.Text>
            Se descarga la rama, se deja en <Typography.Text code>/opt/optidev-preview/{target?.repo.key}</Typography.Text>{' '}
            de la VM y se corre la receta del repo. Lo que haya cargado de ese repo se reemplaza.
          </Typography.Text>
          <Space>
            <Typography.Text>VM:</Typography.Text>
            <Select value={vmId} onChange={setVmId} style={{ width: 360 }} disabled={vms.length < 2}
              options={vms.map((v) => ({ value: v.id, label: `${v.name} · ${v.username}@${v.host}` }))} />
          </Space>
        </Space>
      ) : (
        <>
          <Steps size="small" current={stepIndex} status={stepStatus} items={STEPS} style={{ marginBottom: 12 }} />
          {load.status === 'success' && (
            <Alert type="success" showIcon style={{ marginBottom: 12 }}
              message={`Cargado en ${load.vmName}${load.sha ? ` (${load.sha.slice(0, 7)})` : ''}`} />
          )}
          <pre ref={logRef} style={{
            background: '#0d1117', color: '#c9d1d9', padding: 12, borderRadius: 6, overflow: 'auto',
            height: load.status === 'failed' ? 200 : 360,
            fontSize: 12, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
          }}>
            {load.log || 'Iniciando…'}
          </pre>
          {/* Problemas "tontos" de la VM (sin Docker, sin git, sin disco...) con su botón para resolverlos. */}
          {load.status === 'failed' && vm && (
            <div style={{ marginTop: 16 }}>
              {load.issues?.length > 0 && (
                <Alert type="warning" showIcon style={{ marginBottom: 12 }}
                  message={`Problema detectado: ${load.issues.map((i) => i.title).join(', ')}`}
                  description="Resolvelo con el botón y después apretá Reintentar." />
              )}
              <VmDoctor vm={vm} suggested={load.issues || []} />
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
