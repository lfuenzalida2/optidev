import React, { useEffect, useRef, useState } from 'react';
import { Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

// Mismo tema que la terminal de Helios.
const TERM_THEME = {
  background: '#0d1117', foreground: '#c9d1d9', cursor: '#58a6ff',
  cursorAccent: '#0d1117', selectionBackground: 'rgba(88,166,255,0.3)',
  black: '#484f58', red: '#ff7b72', green: '#3fb950', yellow: '#d29922',
  blue: '#58a6ff', magenta: '#bc8cff', cyan: '#39c5cf', white: '#b1bac4',
  brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364',
  brightYellow: '#e3b341', brightBlue: '#79c0ff', brightMagenta: '#d2a8ff',
  brightCyan: '#56d4dd', brightWhite: '#f0f6fc',
};
// nginx corta un WebSocket sin tráfico (proxy_read_timeout): un ping lo mantiene vivo.
const PING_MS = 25000;

const wsUrl = (vmId) => `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
  + `/api/vms/${vmId}/terminal`;

// Una sesión SSH a una VM. Protocolo en backend/src/services/terminal_service.py:
// la salida llega en frames binarios y los avisos (connected/error/closed) en JSON.
// `command`: se escribe y se ejecuta al conectar la primera vez (no al reconectar).
export default function VmTerminal({ vm, visible, command }) {
  const divRef = useRef(null);
  const fitRef = useRef(null);
  const [ended, setEnded] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setEnded(false);
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: '"Cascadia Code","Fira Code","JetBrains Mono",monospace',
      fontSize: 13,
      lineHeight: 1.2,
      scrollback: 10000,
      theme: TERM_THEME,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(divRef.current);
    fitRef.current = fit;
    const refit = () => { try { fit.fit(); } catch { /* contenedor oculto o sin medir */ } };
    refit();
    const observer = new ResizeObserver(refit);
    observer.observe(divRef.current);

    term.write(`Conectando a ${vm.username}@${vm.host}…\r\n`);
    const ws = new WebSocket(wsUrl(vm.id));
    ws.binaryType = 'arraybuffer';
    const send = (message) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(message));
    let ping;

    ws.onopen = () => {
      refit();
      // El backend no abre la sesión SSH hasta recibir esto (el token no va en la URL).
      ws.send(JSON.stringify({ type: 'auth', token: localStorage.getItem('token'), cols: term.cols, rows: term.rows }));
      ping = setInterval(() => send({ type: 'ping' }), PING_MS);
    };
    ws.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        term.write(new Uint8Array(event.data));
        return;
      }
      const message = JSON.parse(event.data);
      if (message.type === 'connected') {
        term.focus();
        // Si el shell todavía no mostró el prompt, la PTY lo guarda hasta que lo lea.
        if (command && attempt === 0) send({ type: 'input', data: `${command}\r` });
      }
      if (message.type === 'error') term.write(`\r\n\x1b[31m[${message.message}]\x1b[0m\r\n`);
      if (message.type === 'closed') term.write(`\r\n[Sesión terminada (código ${message.exitStatus ?? '?'})]\r\n`);
    };
    ws.onclose = () => {
      clearInterval(ping);
      term.write('\r\n[Desconectado]\r\n');
      setEnded(true);
    };
    const dataSub = term.onData((data) => send({ type: 'input', data }));
    const resizeSub = term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));

    return () => {
      clearInterval(ping);
      dataSub.dispose();
      resizeSub.dispose();
      observer.disconnect();
      ws.onclose = null;
      ws.close();
      term.dispose();
    };
  }, [vm.id, vm.username, vm.host, attempt]);

  // Al volver a mostrarse (cambio de pestaña, panel restaurado) el tamaño pudo cambiar.
  useEffect(() => {
    if (visible) requestAnimationFrame(() => { try { fitRef.current?.fit(); } catch { /* sin medir */ } });
  }, [visible]);

  return (
    <div style={{ position: 'relative', height: '100%', display: visible ? 'block' : 'none' }}>
      <div ref={divRef} style={{ height: '100%', padding: '4px 0 0 6px', background: TERM_THEME.background }} />
      {ended && (
        <Button size="small" icon={<ReloadOutlined />} onClick={() => setAttempt((n) => n + 1)}
          style={{ position: 'absolute', right: 16, bottom: 12 }}>
          Reconectar
        </Button>
      )}
    </div>
  );
}
