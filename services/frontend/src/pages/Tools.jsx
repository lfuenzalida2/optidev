import React, { useEffect, useState } from 'react';
import { Alert, App, Button, Card, Input, Select, Space, Typography } from 'antd';
import { CodeOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';

import { toolsApi, vmsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import VmDoctor from '../components/VmDoctor';
import { useAuth } from '../contexts/AuthContext';
import { useTerminals } from '../contexts/TerminalContext';

// navigator.clipboard solo existe en HTTPS o localhost; la app se sirve por
// HTTP, así que se cae a execCommand('copy') (deprecado pero soportado).
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  const el = document.createElement('textarea');
  el.value = text;
  el.setAttribute('readonly', '');
  el.style.position = 'fixed';
  el.style.opacity = '0';
  document.body.appendChild(el);
  el.select();
  try {
    return document.execCommand('copy');
  } finally {
    document.body.removeChild(el);
  }
}

function Md5Card() {
  const { message } = App.useApp();
  const [text, setText] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);

  const compute = async () => {
    setLoading(true);
    try {
      const data = await toolsApi.md5(text);
      setResult(data);
      try {
        if (await copyText(data.md5)) message.success('MD5 copiado al portapapeles');
      } catch {
        // Sin permiso para copiar: queda el botón de copiar junto al hash.
      }
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card title="MD5">
      <Typography.Paragraph type="secondary">
        Calcula el hash MD5 (hex) de un texto, codificado en UTF-8.
      </Typography.Paragraph>
      <Space direction="vertical" style={{ width: '100%' }}>
        <Input.TextArea
          value={text}
          onChange={(e) => { setText(e.target.value); setResult(null); }}
          // Enter calcula; Shift+Enter agrega un salto de línea.
          onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); compute(); } }}
          placeholder="Texto a hashear (Enter calcula, Shift+Enter nueva línea)"
          autoSize={{ minRows: 2, maxRows: 8 }}
        />
        <Button type="primary" loading={loading} onClick={compute}>Calcular MD5</Button>
        {result && (
          <Typography.Text code copyable style={{ fontSize: 15 }}>{result.md5}</Typography.Text>
        )}
      </Space>
    </Card>
  );
}

// Herramienta que se instala en una VM de pruebas: elige la VM y muestra los
// pasos del grupo `group` del catálogo del backend (vm_doctor_service).
function VmToolCard({ title, description, group, doneMessage, footer, onFixed }) {
  const { message } = App.useApp();
  const { openTerminal } = useTerminals();
  const [vms, setVms] = useState(null);
  const [vmId, setVmId] = useState(null);

  useEffect(() => {
    vmsApi.list()
      .then((list) => { setVms(list); if (list.length) setVmId(list[0].id); })
      .catch((err) => { setVms([]); message.error(extractErrorMessage(err)); });
  }, [message]);

  const vm = vms?.find((v) => v.id === vmId);

  return (
    <Card title={title} style={{ marginTop: 16 }}>
      <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
      {vms && !vms.length && (
        <Alert type="info" showIcon
          message={<>No tenés VMs de pruebas. Agregá una en <Link to="/account?seccion=vm">Mi perfil</Link>.</>} />
      )}
      {vms?.length > 0 && (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Space wrap>
            <Select style={{ minWidth: 240 }} value={vmId} onChange={setVmId}
              options={vms.map((v) => ({ value: v.id, label: `${v.name} (${v.host})` }))} />
            <Button icon={<CodeOutlined />} disabled={!vm} onClick={() => openTerminal(vm)}>Terminal</Button>
          </Space>
          {vm && (
            <VmDoctor key={vm.id} vm={vm} group={group} steps title={`Pasos en ${vm.name}`}
              doneMessage={doneMessage} onFixed={onFixed} />
          )}
          {footer && <Typography.Text type="secondary">{footer}</Typography.Text>}
        </Space>
      )}
    </Card>
  );
}

// Deja Claude Code Remote Control corriendo fijo (servicio de systemd) en una
// VM de pruebas. El login de claude.ai es de cada persona y se hace a mano en la terminal.
function ClaudeRcCard() {
  return (
    <VmToolCard title="Claude Remote Control en mi VM" group="claude_rc"
      description={'Deja Claude Code corriendo siempre en tu VM de pruebas (sigue vivo aunque cierres la '
        + 'terminal y arranca con la VM), para usarlo desde claude.ai/code o la app de Claude.'}
      doneMessage="Remote Control está activo: la VM aparece en claude.ai/code"
      footer={'Ojo: quien tenga acceso a tu cuenta de claude.ai puede ejecutar comandos en la VM. Los permisos '
        + 'quedan en modo «default» (Claude pide confirmación antes de cambiar cosas).'} />
  );
}

// Rutina diaria (timer de systemd en la VM): conecta el MCP de optidev con un
// token propio de la VM y programa Claude Code de lunes a viernes a la 1:00.
// Necesita Claude instalado y con sesión iniciada (pasos 1 y 2 de Remote Control).
function RoutineToolCard() {
  const { refreshUser } = useAuth();
  return (
    // Instalar la rutina la prueba una vez (report_routine_run): se relee el usuario para verla en Mi perfil.
    <VmToolCard title="Rutina diaria en mi VM" group="routine" onFixed={refreshUser}
      description={'Programa Claude Code en tu VM de pruebas para que de lunes a viernes a la 1:00 (hora de '
        + 'Chile) revise tus tarjetas de «Pendiente» y «En proceso», avance las que pueda y abra PRs. Antes '
        + 'instalá Claude Code e iniciá sesión (pasos 1 y 2 de Claude Remote Control).'}
      doneMessage="Rutina instalada: corre de lunes a viernes a la 1:00"
      footer={'Corre sin pedir permisos (crea ramas, corre tests y abre PRs). Conectar el MCP crea un token '
        + 'nuevo en Mi perfil («Rutina diaria (<VM>)») y revoca el anterior de esa VM.'} />
  );
}

export default function Tools() {
  return (
    <div style={{ padding: 16, maxWidth: 720 }}>
      <Md5Card />
      <ClaudeRcCard />
      <RoutineToolCard />
    </div>
  );
}
