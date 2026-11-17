import React, { useEffect, useRef, useState } from 'react';
import { Alert, App, Button, Input, Modal, Select, Space, Typography } from 'antd';
import { RobotOutlined, SendOutlined } from '@ant-design/icons';

import { vmsApi } from '../api';
import { extractErrorMessage } from '../api/axios';

const POLL_MS = 1500;

// Corre una skill de Claude (`claude -p`) sobre un PR en la VM de pruebas del usuario y muestra el log.
// Cuando Claude termina un turno se le puede responder (misma sesión y mismo log) hasta dar la revisión por
// terminada: por ejemplo para que suba los cambios al PR o corrija algo.
// `target` = { skill, card, pull } (null = cerrado).
export default function SkillRunModal({ target, vms, onClose, onFinished }) {
  const { message } = App.useApp();
  const [vmId, setVmId] = useState(null);
  const [task, setTask] = useState(null);
  const [starting, setStarting] = useState(false);
  const [reply, setReply] = useState('');
  const [sending, setSending] = useState(false);
  const logRef = useRef(null);

  useEffect(() => { setTask(null); setReply(''); }, [target]);

  useEffect(() => {
    setVmId((current) => (vms.some((v) => v.id === current) ? current : vms[0]?.id ?? null));
  }, [vms]);

  // Mientras corre, se consulta el estado cada tanto.
  useEffect(() => {
    if (!task || task.status !== 'running') return undefined;
    const timer = setTimeout(async () => {
      try {
        const next = await vmsApi.task(task.id);
        setTask(next);
        if (next.status !== 'running') onFinished();
      } catch (err) {
        message.error(extractErrorMessage(err));
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [task, message, onFinished]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [task?.log]);

  const start = async () => {
    setStarting(true);
    try {
      setTask(await vmsApi.runSkill(vmId, target.skill, target.card.id, target.pull.url));
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setStarting(false);
    }
  };

  const send = async () => {
    setSending(true);
    try {
      setTask(await vmsApi.replySkill(task.id, reply));
      setReply('');
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setSending(false);
    }
  };

  const running = task?.status === 'running';
  return (
    <Modal open={!!target} width={720} destroyOnClose onCancel={onClose}
      title={target && <Space><RobotOutlined />{`Correr ${target.skill} · #${target.card.id} ${target.card.title}`}</Space>}
      footer={[
        <Button key="close" onClick={onClose}>{running ? 'Cerrar (sigue corriendo)' : 'Cerrar'}</Button>,
        !task && <Button key="run" type="primary" icon={<RobotOutlined />} loading={starting} disabled={!vmId}
          onClick={start}>Correr</Button>,
      ]}>
      {!task && (
        <>
          <Typography.Paragraph>
            Corre <Typography.Text code>claude -p</Typography.Text> en tu VM de pruebas, sin pedir permisos, para que
            use la skill <Typography.Text code>{target?.skill}</Typography.Text> sobre{' '}
            <a href={target?.pull.url} target="_blank" rel="noreferrer">{target?.pull.url}</a>. Puede tardar varios
            minutos y la skill puede comentar y poner labels en el PR. No mergea.
          </Typography.Paragraph>
          {vms.length === 0
            ? <Alert type="warning" showIcon message="Primero cargá una VM de pruebas en Mi perfil." />
            : (
              <Select style={{ width: '100%' }} value={vmId} onChange={setVmId}
                options={vms.map((v) => ({ value: v.id, label: `${v.name} (${v.username}@${v.host})` }))} />
            )}
        </>
      )}
      {task && (
        <>
          {task.status !== 'running' && (
            <Alert style={{ marginBottom: 8 }} showIcon type={task.status === 'success' ? 'success' : 'error'}
              message={task.status === 'success'
                ? `${task.title}: Claude respondió. Contestale abajo o cerrá cuando esté listo`
                : `${task.title}: falló (ver log)`} />
          )}
          <pre ref={logRef} style={{ maxHeight: 360, overflow: 'auto', background: '#1f1f1f', color: '#d9d9d9',
            padding: 12, borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12 }}>
            {task.log || 'Iniciando…'}
          </pre>
          {task.canReply && (
            <Space.Compact style={{ width: '100%', marginTop: 8 }}>
              <Input.TextArea value={reply} onChange={(e) => setReply(e.target.value)} maxLength={4000}
                autoSize={{ minRows: 2, maxRows: 8 }} placeholder="Responder a Claude… (Ctrl+Enter para enviar)"
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && reply.trim() && !sending) send(); }} />
              <Button type="primary" icon={<SendOutlined />} loading={sending} disabled={!reply.trim()}
                onClick={send} style={{ height: 'auto' }}>Enviar</Button>
            </Space.Compact>
          )}
        </>
      )}
    </Modal>
  );
}
