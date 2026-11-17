import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Alert, Button, ConfigProvider, Popconfirm, Select, Space, Spin, Tooltip, Typography, theme as antdTheme,
} from 'antd';
import { MinusOutlined, PlayCircleOutlined, PoweroffOutlined, RobotOutlined } from '@ant-design/icons';

import { useTerminals } from '../../contexts/TerminalContext';
import VmTerminal from './VmTerminal';

const BAR = { background: '#161b22', color: '#c9d1d9', borderBottom: '1px solid #30363d' };
const ICON_BTN = { color: 'inherit' };
const PREFS_KEY = 'claudeChatPrefs';

// Qué puede hacer Claude sin preguntar (`claude --permission-mode`); cambiarlo reinicia la sesión.
// No se ofrece "sin confirmaciones" (bypassPermissions): dejaría a Claude mergear PRs y mover tarjetas sin preguntar.
const PERMISSIONS = [
  { value: 'default', label: 'Pregunta antes de cada acción', hint: 'Pide permiso para editar archivos, correr comandos y usar el MCP de optidev.' },
  { value: 'acceptEdits', label: 'Acepta ediciones de archivos', hint: 'Edita archivos sin preguntar; el resto (comandos, MCP) lo sigue consultando.' },
  { value: 'plan', label: 'Solo planifica', hint: 'Lee y propone un plan, sin editar ni ejecutar nada.' },
];

// Contexto fijo del chat. Va entre comillas simples en el shell: no puede llevar comillas simples.
const SYSTEM_PROMPT = 'Sos el asistente del tablero optidev del usuario. Respondé en español. '
  + 'Para consultar o mover tarjetas usá las tools del MCP optidev; para resolver tarjetas, la skill quick-tasks-runner; '
  + 'para PRs, revisar-pr y pr-review-merge. Nunca muevas una tarjeta a Hecho.';

// "default" no lleva flag: es lo que hace Claude sin él en cualquier versión (el nombre del modo cambió entre versiones).
const command = (mode) => `claude${mode === 'default' ? '' : ` --permission-mode ${mode}`} --append-system-prompt '${SYSTEM_PROMPT}'`;

const vmLabel = (vm) => `${vm.username}@${vm.host}${vm.port !== 22 ? `:${vm.port}` : ''}`;

const readPrefs = () => {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY)) || {};
  } catch {
    return {};
  }
};

// Terminal flotante (en el dock de Layout, a la izquierda del botón de las terminales SSH) que corre Claude Code en
// una de las VMs del usuario. Minimizada la sesión sigue viva, igual que las terminales SSH.
export default function ClaudeChat() {
  const { vms, loadVms } = useTerminals();
  const [open, setOpen] = useState(false);
  const [prefs, setPrefs] = useState(readPrefs);
  const [session, setSession] = useState(null); // { vm, mode, id }: sin sesión se muestra la configuración

  useEffect(() => { if (open) loadVms(); }, [open, loadVms]);

  const savePrefs = useCallback((changes) => {
    setPrefs((prev) => {
      const next = { ...prev, ...changes };
      try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* sin storage */ }
      return next;
    });
  }, []);

  const mode = PERMISSIONS.some((p) => p.value === prefs.mode) ? prefs.mode : 'default';
  const vm = (vms || []).find((v) => v.id === prefs.vmId) || (vms || [])[0];

  const start = () => setSession({ vm, mode, id: Date.now() });
  const stop = () => setSession(null);

  // Cambiar los permisos con la sesión en curso la reinicia (Claude los fija al arrancar).
  const changeMode = (value) => {
    savePrefs({ mode: value });
    if (session) setSession({ ...session, mode: value, id: Date.now() });
  };

  return (
    <>
      {!open && (
        <Button type="primary" shape="circle" size="large" icon={<RobotOutlined />} aria-label="Abrir el chat de Claude"
          onClick={() => setOpen(true)}
          style={{ pointerEvents: 'auto', boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }} />
      )}
      {/* Minimizado sigue montado (oculto): la sesión SSH de Claude no se corta. */}
      <div style={{
        display: open ? 'flex' : 'none', pointerEvents: 'auto',
        flex: '0 1 440px', minWidth: 0, flexDirection: 'column', overflow: 'hidden',
        height: 'min(520px, calc(100vh - 96px))',
        borderRadius: 8, border: '1px solid #30363d', boxShadow: '0 8px 24px rgba(0,0,0,0.35)', background: '#0d1117',
      }}>
        <div style={{ ...BAR, display: 'flex', alignItems: 'center', gap: 4, padding: '0 4px 0 10px', height: 34 }}>
          <RobotOutlined style={{ marginRight: 4 }} />
          <Typography.Text style={{ color: 'inherit', fontSize: 12, flex: 1 }}>
            Claude{session ? ` · ${session.vm.name}` : ''}
          </Typography.Text>
          {session && (
            <Popconfirm title="¿Terminar la sesión de Claude?" description="Se pierde la conversación." okText="Terminar"
              cancelText="Cancelar" onConfirm={stop}>
              <Button type="text" size="small" style={ICON_BTN} icon={<PoweroffOutlined />} aria-label="Terminar la sesión" />
            </Popconfirm>
          )}
          <Tooltip title="Minimizar">
            <Button type="text" size="small" style={ICON_BTN} icon={<MinusOutlined />} onClick={() => setOpen(false)} />
          </Tooltip>
        </div>
        <div style={{ ...BAR, padding: '6px 10px' }}>
          {/* Tema oscuro para que el Select no desentone con la terminal. */}
          <ConfigProvider theme={{ algorithm: antdTheme.darkAlgorithm }}>
            <Select size="small" style={{ width: '100%' }} value={mode} onChange={changeMode}
              options={PERMISSIONS.map(({ value, label }) => ({ value, label }))} aria-label="Permisos de Claude" />
          </ConfigProvider>
          <Typography.Text style={{ color: '#8b949e', fontSize: 11 }}>
            {PERMISSIONS.find((p) => p.value === mode).hint}
          </Typography.Text>
        </div>
        <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
          {session && <VmTerminal key={session.id} vm={session.vm} command={command(session.mode)} visible={open} />}
          {!session && (
            <div style={{ height: '100%', padding: 16, color: '#c9d1d9', display: 'flex', flexDirection: 'column', gap: 12 }}>
              {vms === null && <Spin />}
              {vms && !vms.length && (
                <Alert type="info" showIcon message="No tenés VMs cargadas"
                  description={<>Claude corre en una de tus VMs. Cargala en <Link to="/account?seccion=vm" onClick={() => setOpen(false)}>Mi perfil</Link>.</>} />
              )}
              {vms && vms.length > 0 && (
                <>
                  <Typography.Text style={{ color: 'inherit' }}>VM donde corre Claude</Typography.Text>
                  <ConfigProvider theme={{ algorithm: antdTheme.darkAlgorithm }}>
                    <Select value={vm.id} onChange={(vmId) => savePrefs({ vmId })} aria-label="VM de Claude"
                      options={vms.map((v) => ({ value: v.id, label: `${v.name} · ${vmLabel(v)}` }))} />
                  </ConfigProvider>
                  <Space>
                    <Button type="primary" icon={<PlayCircleOutlined />} onClick={start}>Iniciar Claude</Button>
                  </Space>
                  <Typography.Text style={{ color: '#8b949e', fontSize: 12 }}>
                    La VM necesita Claude Code, las skills de Optidev y el MCP de optidev (Mi perfil → VM de prueba →
                    Diagnóstico). Claude conoce el tablero por el MCP y resuelve tarjetas y PRs con esas skills.
                  </Typography.Text>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
