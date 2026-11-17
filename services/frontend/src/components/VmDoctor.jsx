import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, App, Button, List, Space, Spin, Typography } from 'antd';
import { CheckCircleFilled, CloseCircleFilled, CodeOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons';

import { vmsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { useTerminals } from '../contexts/TerminalContext';

const POLL_MS = 1000;
const LOG_STYLE = {
  background: '#0d1117', color: '#c9d1d9', padding: 12, borderRadius: 6, maxHeight: 280, overflow: 'auto',
  fontSize: 12, margin: '8px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
};

// Diagnóstico de la VM de pruebas: chequea los problemas conocidos (git,
// Docker, disco...) y ofrece "Resolver" para los que tienen arreglo. Los
// arreglos son del catálogo del backend: acá solo se elige cuál.
// `suggested` = problemas que ya se vieron en el log de una carga fallida.
// `group` = qué parte del catálogo chequear; con `steps` se muestran todos los
// chequeos en orden (también los que están bien), como pasos de una instalación.
// `alwaysFix` = ids cuyo arreglo se ofrece aunque el chequeo esté bien (p. ej. actualizar a mano).
export default function VmDoctor({ vm, suggested = [], onFixed, group = 'doctor', title, steps = false, doneMessage, alwaysFix = [] }) {
  const { message } = App.useApp();
  const { openTerminal } = useTerminals();
  const [checks, setChecks] = useState(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [error, setError] = useState(null);
  const [task, setTask] = useState(null);
  const logRef = useRef(null);

  const diagnose = useCallback(async () => {
    setDiagnosing(true);
    setError(null);
    try {
      setChecks((await vmsApi.diagnose(vm.id, group)).checks);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setDiagnosing(false);
    }
  }, [vm.id, group]);

  useEffect(() => { setTask(null); diagnose(); }, [diagnose]);

  useEffect(() => {
    if (!task || task.status !== 'running') return undefined;
    const timer = setTimeout(async () => {
      try {
        const next = await vmsApi.task(task.id);
        setTask(next);
        if (next.status !== 'running') {
          diagnose();
          if (next.status === 'success') onFixed?.(next);
        }
      } catch (err) {
        message.error(extractErrorMessage(err));
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [task, diagnose, message, onFixed]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [task?.log]);

  const fix = async (issueId) => {
    try {
      setTask(await vmsApi.fix(vm.id, issueId));
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  // Primero lo que se vio en el log; si el diagnóstico todavía no volvió, igual se puede resolver.
  const failing = (checks || []).filter((c) => !c.ok);
  const suggestedIds = new Set(suggested.map((s) => s.id));
  let items = suggested;
  if (checks) items = steps ? checks : [...failing].sort((a, b) => suggestedIds.has(b.id) - suggestedIds.has(a.id));
  const busy = task?.status === 'running';

  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Typography.Text strong>{title || `Diagnóstico de ${vm.name}`}</Typography.Text>
        <Button size="small" icon={<ReloadOutlined />} loading={diagnosing} disabled={busy} onClick={diagnose}>
          Volver a chequear
        </Button>
      </Space>
      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 8 }} />}
      {diagnosing && !checks && !items.length && <Spin size="small" />}
      {checks && !failing.length && (
        <Alert type="success" showIcon icon={<CheckCircleFilled />} style={{ marginBottom: 8 }}
          message={doneMessage || 'No se encontraron problemas conocidos'} />
      )}
      {items.length > 0 && (
        <List size="small" bordered dataSource={items} renderItem={(issue) => (
          <List.Item actions={issue.ok && !(alwaysFix.includes(issue.id) && issue.fixTitle) ? [] : issue.fixTitle ? [
            <Button key="fix" size="small" type={issue.ok ? 'default' : 'primary'} icon={<ToolOutlined />} disabled={busy}
              loading={busy && task.issue === issue.id} onClick={() => fix(issue.id)}>
              {issue.fixTitle}
            </Button>,
          ] : issue.command ? [
            // Paso a mano en la VM: abre la terminal con el comando ya escrito y ejecutado.
            <Button key="term" size="small" type="primary" icon={<CodeOutlined />} disabled={busy}
              onClick={() => openTerminal(vm, issue.command)}>
              Abrir en la terminal
            </Button>,
          ] : [<Typography.Text key="manual" type="secondary">Hay que resolverlo a mano</Typography.Text>]}>
            <Space direction="vertical" size={2}>
              <Space>
                {issue.ok
                  ? <CheckCircleFilled style={{ color: '#52c41a' }} />
                  : <CloseCircleFilled style={{ color: '#f5222d' }} />}
                {issue.title}
                {suggestedIds.has(issue.id) && <Typography.Text type="secondary">(causó la falla)</Typography.Text>}
              </Space>
              {!issue.ok && issue.hint && <Typography.Text type="secondary">{issue.hint}</Typography.Text>}
            </Space>
          </List.Item>
        )} />
      )}
      {task && (
        <>
          {task.status !== 'running' && (
            <Alert style={{ marginTop: 8 }} showIcon type={task.status === 'success' ? 'success' : 'error'}
              message={task.status === 'success' ? `${task.title}: listo` : `${task.title}: falló (ver log)`} />
          )}
          <pre ref={logRef} style={LOG_STYLE}>{task.log || 'Iniciando…'}</pre>
        </>
      )}
    </div>
  );
}
