import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Descriptions, Modal, Popconfirm, Tag, Typography } from 'antd';
import { CloudSyncOutlined } from '@ant-design/icons';

import { systemApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { colors } from '../theme';

const STEPS = {
  fetch: 'Buscando cambios',
  pull: 'Bajando cambios',
  dependencies: 'Dependencias',
  migrations: 'Respaldo y migraciones',
  frontend: 'Build del frontend',
  restart: 'Reinicio',
  rollback: 'Volviendo atrás',
  none: 'Sin cambios',
  done: 'Listo',
};

const STATE_TAG = {
  running: <Tag color="processing">En curso</Tag>,
  success: <Tag color="success">OK</Tag>,
  failed: <Tag color="error">Falló</Tag>,
};

const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : '—');
const short = (sha) => (sha ? sha.slice(0, 7) : '—');

// Botón (solo admins) para actualizar la app desde master. El backend solo
// deja un pedido; lo ejecuta deploy/update.sh en el servidor (ver README.md, sección "Actualización desde la app").
export default function UpdateButton() {
  const { message } = App.useApp();
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState(null);
  const [requesting, setRequesting] = useState(false);
  // Se recarga la página solo si la actualización exitosa fue pedida desde acá.
  const waitingRef = useRef(false);

  const busy = !!info && (info.pending || info.running);

  const refresh = useCallback(async () => {
    try {
      const next = await systemApi.updateStatus();
      setInfo(next);
      if (waitingRef.current && !next.pending && !next.running && next.status?.state !== 'running') {
        waitingRef.current = false;
        if (next.status?.state === 'success' && next.status?.step === 'done') {
          message.success('Actualizado. Recargando…');
          setTimeout(() => window.location.reload(), 1500);
        } else if (next.status?.state === 'success') {
          message.info(next.status.message);
        } else {
          message.error(next.status?.message || 'La actualización falló');
        }
      }
    } catch {
      // Durante el reinicio el backend no responde: se reintenta en el próximo ciclo.
    }
  }, [message]);

  useEffect(() => {
    if (!open && !waitingRef.current) return undefined;
    refresh();
    const id = setInterval(refresh, busy || waitingRef.current ? 2000 : 10000);
    return () => clearInterval(id);
  }, [open, busy, refresh]);

  const request = async () => {
    setRequesting(true);
    try {
      setInfo(await systemApi.requestUpdate());
      waitingRef.current = true;
      message.info('Actualización pedida');
    } catch (error) {
      message.error(extractErrorMessage(error));
    } finally {
      setRequesting(false);
    }
  };

  const status = info?.status;

  return (
    <>
      <Button
        type="text"
        icon={<CloudSyncOutlined spin={busy} />}
        style={{ color: colors.onDark }}
        onClick={() => setOpen(true)}
      >
        Actualizar
      </Button>
      <Modal
        title="Actualizar Optidev"
        open={open}
        onCancel={() => setOpen(false)}
        width={760}
        footer={[
          <Button key="close" onClick={() => setOpen(false)}>Cerrar</Button>,
          <Popconfirm
            key="go"
            title="¿Actualizar desde master?"
            description="Se bajan los cambios, se migra la base y se reinicia el backend."
            okText="Actualizar"
            cancelText="Cancelar"
            onConfirm={request}
            disabled={!info?.available || busy}
          >
            <Button type="primary" loading={requesting || busy} disabled={!info?.available || busy}>
              {busy ? 'Actualizando…' : 'Actualizar ahora'}
            </Button>
          </Popconfirm>,
        ]}
      >
        {info && !info.available && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message='La actualización automática no está instalada en el servidor (ver README.md, sección "Actualización desde la app").'
          />
        )}
        {info?.pending && (
          <Alert type="info" showIcon style={{ marginBottom: 16 }} message="Pedido enviado, esperando al servidor…" />
        )}
        {status ? (
          <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Estado">{STATE_TAG[status.state] || status.state}</Descriptions.Item>
            <Descriptions.Item label="Paso">{STEPS[status.step] || status.step}</Descriptions.Item>
            <Descriptions.Item label="Mensaje" span={2}>{status.message}</Descriptions.Item>
            <Descriptions.Item label="Versión">
              {short(status.fromCommit)} → {short(status.toCommit)}
            </Descriptions.Item>
            <Descriptions.Item label="Inicio">{fmt(status.startedAt)}</Descriptions.Item>
            {info.lastRequest && (
              <Descriptions.Item label="Último pedido" span={2}>
                {info.lastRequest.requestedBy} · {fmt(info.lastRequest.requestedAt)}
              </Descriptions.Item>
            )}
          </Descriptions>
        ) : (
          info?.available && <Typography.Paragraph type="secondary">Todavía no se corrió ninguna actualización.</Typography.Paragraph>
        )}
        {info?.log && (
          <pre style={{ maxHeight: 320, overflow: 'auto', background: colors.pageBg, padding: 12, fontSize: 12, margin: 0 }}>
            {info.log}
          </pre>
        )}
      </Modal>
    </>
  );
}
