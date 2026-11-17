import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Button, ConfigProvider, Empty, Select, Spin, Tooltip, Typography, theme as antdTheme,
} from 'antd';
import {
  CloseOutlined, CodeOutlined, FullscreenExitOutlined, FullscreenOutlined, MinusOutlined, PlusOutlined,
} from '@ant-design/icons';

import { layoutTree, leaves, useTerminals } from '../../contexts/TerminalContext';
import VmTerminal from './VmTerminal';

const BAR = { background: '#161b22', color: '#c9d1d9', borderBottom: '1px solid #30363d' };
const ICON_BTN = { color: 'inherit' };
const MIN_W = 420;
const MIN_H = 220;
const SIZE_KEY = 'terminalPanelSize';

const SplitIcon = ({ dir }) => (
  <svg width="13" height="13" viewBox="0 0 12 12" fill="currentColor" style={{ display: 'block' }}>
    {dir === 'h' ? (
      <><rect x="0" y="1" width="5" height="10" rx="1" opacity=".6" />
        <rect x="7" y="1" width="5" height="10" rx="1" opacity=".6" /></>
    ) : (
      <><rect x="1" y="0" width="10" height="5" rx="1" opacity=".6" />
        <rect x="1" y="7" width="10" height="5" rx="1" opacity=".6" /></>
    )}
  </svg>
);

const vmLabel = (vm) => `${vm.username}@${vm.host}${vm.port !== 22 ? `:${vm.port}` : ''}`;

const readSize = () => {
  try {
    const saved = JSON.parse(localStorage.getItem(SIZE_KEY));
    if (saved?.w >= MIN_W && saved?.h >= MIN_H) return saved;
  } catch { /* sin storage o valor inválido */ }
  return { w: 900, h: 480 };
};

// Panel recién creado por un split: se elige la VM ahí mismo.
function EmptyPane({ onSelect }) {
  const { vms, loadVms } = useTerminals();
  const navigate = useNavigate();
  useEffect(() => { loadVms(); }, [loadVms]);
  return (
    <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#c9d1d9' }}>
      {vms === null ? <Spin /> : vms.length ? (
        // Tema oscuro para que el Select no desentone con la terminal.
        <ConfigProvider theme={{ algorithm: antdTheme.darkAlgorithm }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ marginBottom: 8 }}>Elegí la VM para este panel</div>
            <Select
              autoFocus showSearch placeholder="Seleccioná una VM" style={{ width: 'min(340px, 80%)', minWidth: 220 }}
              optionFilterProp="search"
              options={vms.map((vm) => ({
                value: vm.id,
                search: `${vm.name} ${vmLabel(vm)}`,
                label: <span>{vm.name} <Typography.Text type="secondary" style={{ fontSize: 12 }}>{vmLabel(vm)}</Typography.Text></span>,
              }))}
              onChange={(id) => onSelect(vms.find((vm) => vm.id === id))}
            />
          </div>
        </ConfigProvider>
      ) : (
        <Empty description={<span style={{ color: '#c9d1d9' }}>No tenés VMs cargadas</span>}>
          <Button size="small" onClick={() => navigate('/account?seccion=vm')}>Ir a Mi perfil</Button>
        </Empty>
      )}
    </div>
  );
}

// Arrastre genérico: llama a onMove(dx, dy, evento) mientras se mueve el mouse.
// Mientras dura, una capa transparente tapa las terminales para que xterm no
// se quede con los eventos ni seleccione texto.
function useDrag() {
  const [dragging, setDragging] = useState(null); // cursor CSS mientras se arrastra
  const start = useCallback((event, cursor, onMove) => {
    event.preventDefault();
    const x0 = event.clientX;
    const y0 = event.clientY;
    const move = (e) => onMove(e.clientX - x0, e.clientY - y0, e);
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      setDragging(null);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
    setDragging(cursor);
  }, []);
  return [dragging, start];
}

function TabPanes({ tab, visible, startDrag }) {
  const { setActiveLeaf, setPaneVm, closePane, setRatio } = useTerminals();
  const containerRef = useRef(null);
  const { panes, dividers } = layoutTree(tab.tree);
  const multiple = panes.length > 1;

  const dragDivider = (event, divider) => {
    const box = containerRef.current.getBoundingClientRect();
    const horizontal = divider.split.dir === 'h';
    startDrag(event, horizontal ? 'col-resize' : 'row-resize', (dx, dy, e) => {
      // Posición del mouse en % del contenedor → ratio dentro del rectángulo del split.
      const pos = horizontal ? ((e.clientX - box.left) / box.width) * 100 : ((e.clientY - box.top) / box.height) * 100;
      const { parent } = divider;
      const ratio = horizontal ? (pos - parent.x) / parent.w : (pos - parent.y) / parent.h;
      setRatio(tab.key, divider.split.id, Math.min(0.85, Math.max(0.15, ratio)));
    });
  };

  return (
    <div ref={containerRef} style={{ position: 'absolute', inset: 0, display: visible ? 'block' : 'none' }}>
      {panes.map(({ leaf, rect }) => {
        const active = multiple && leaf.id === tab.activeLeaf;
        return (
          <div key={leaf.id} onMouseDown={() => setActiveLeaf(tab.key, leaf.id)}
            style={{
              position: 'absolute', left: `${rect.x}%`, top: `${rect.y}%`, width: `${rect.w}%`, height: `${rect.h}%`,
              display: 'flex', flexDirection: 'column', boxSizing: 'border-box', padding: multiple ? 1 : 0,
              outline: active ? '1px solid #1f6feb' : 'none', outlineOffset: -1,
            }}>
            {multiple && (
              <div style={{ ...BAR, display: 'flex', alignItems: 'center', height: 22, padding: '0 4px 0 8px', fontSize: 11 }}>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {leaf.vm ? `${leaf.vm.name} · ${vmLabel(leaf.vm)}` : 'Panel nuevo'}
                </span>
                <CloseOutlined style={{ fontSize: 10, cursor: 'pointer' }}
                  onClick={(e) => { e.stopPropagation(); closePane(tab.key, leaf.id); }} />
              </div>
            )}
            <div style={{ flex: 1, minHeight: 0 }}>
              {leaf.vm
                ? <VmTerminal vm={leaf.vm} command={leaf.command} visible={visible} />
                : <EmptyPane onSelect={(vm) => setPaneVm(tab.key, leaf.id, vm)} />}
            </div>
          </div>
        );
      })}
      {dividers.map((d) => {
        const horizontal = d.split.dir === 'h';
        return (
          <div key={d.split.id} onMouseDown={(e) => dragDivider(e, d)}
            style={{
              position: 'absolute', zIndex: 2, background: 'transparent',
              cursor: horizontal ? 'col-resize' : 'row-resize',
              ...(horizontal
                ? { left: `calc(${d.at}% - 3px)`, top: `${d.parent.y}%`, width: 6, height: `${d.parent.h}%` }
                : { top: `calc(${d.at}% - 3px)`, left: `${d.parent.x}%`, height: 6, width: `${d.parent.w}%` }),
            }} />
        );
      })}
    </div>
  );
}

// Terminales abajo a la derecha: sin sesiones es solo un botón "+"; con
// sesiones, un panel con pestañas, paneles divididos y bordes para redimensionar.
export default function TerminalPanel() {
  const { tabs, activeKey, setActiveKey, minimized, setMinimized, openTerminal, closeTab, closeAll, splitActive } = useTerminals();
  const [maximized, setMaximized] = useState(false);
  const [size, setSize] = useState(readSize);
  const [dragging, startDrag] = useDrag();

  useEffect(() => {
    try { localStorage.setItem(SIZE_KEY, JSON.stringify(size)); } catch { /* sin storage */ }
  }, [size]);

  if (!tabs.length) {
    // Abre una pestaña vacía: la VM se elige adentro, igual que en un split.
    return (
      <Button type="primary" shape="circle" size="large" icon={<PlusOutlined />} aria-label="Abrir terminal"
        onClick={() => openTerminal(null)}
        style={{ pointerEvents: 'auto', boxShadow: '0 4px 12px rgba(0,0,0,0.3)' }} />
    );
  }

  const activeTab = tabs.find((t) => t.key === activeKey) || tabs[0];

  // Anclado abajo a la derecha del dock: se agranda arrastrando el borde de arriba, el
  // de la izquierda o la esquina de arriba a la izquierda.
  const resize = (event, edges, cursor) => {
    const { w, h } = size;
    startDrag(event, cursor, (dx, dy) => setSize({
      w: edges.includes('left') ? Math.min(window.innerWidth - 32, Math.max(MIN_W, w - dx)) : w,
      h: edges.includes('top') ? Math.min(window.innerHeight - 96, Math.max(MIN_H, h - dy)) : h,
    }));
  };

  // Agrandado ocupa el espacio que deja Claude en el dock; si no, se encoge antes de taparlo.
  const box = maximized
    ? { flex: 1, minWidth: 0, height: 'calc(100vh - 88px)' }
    : { flex: '0 1 auto', minWidth: 0, width: Math.min(size.w, window.innerWidth - 32), height: Math.min(size.h, window.innerHeight - 96) };
  const resizable = !maximized && !minimized;

  return (
    <div style={{
      position: 'relative', pointerEvents: 'auto', display: 'flex', flexDirection: 'column', overflow: 'hidden',
      borderRadius: 8, border: '1px solid #30363d', boxShadow: '0 8px 24px rgba(0,0,0,0.35)', background: '#0d1117',
      ...(minimized ? { flex: '0 0 auto', width: 320 } : box),
    }}>
      {resizable && (
        <>
          <div onMouseDown={(e) => resize(e, ['top'], 'ns-resize')}
            style={{ position: 'absolute', top: 0, left: 10, right: 0, height: 5, cursor: 'ns-resize', zIndex: 3 }} />
          <div onMouseDown={(e) => resize(e, ['left'], 'ew-resize')}
            style={{ position: 'absolute', top: 10, left: 0, bottom: 0, width: 5, cursor: 'ew-resize', zIndex: 3 }} />
          <div onMouseDown={(e) => resize(e, ['top', 'left'], 'nwse-resize')}
            style={{ position: 'absolute', top: 0, left: 0, width: 12, height: 12, cursor: 'nwse-resize', zIndex: 4 }} />
        </>
      )}
      <div style={{ ...BAR, display: 'flex', alignItems: 'center', gap: 2, padding: '0 4px 0 10px', height: 34 }}
        onDoubleClick={() => !minimized && setMaximized(!maximized)}>
        <CodeOutlined style={{ marginRight: 4 }} />
        <div style={{ display: 'flex', flex: 1, minWidth: 0, overflowX: 'auto', gap: 2 }}>
          {tabs.map((t) => {
            const vms = leaves(t.tree).map((l) => l.vm?.name).filter(Boolean);
            return (
              <div key={t.key} onClick={() => { setActiveKey(t.key); setMinimized(false); }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px 4px 10px', cursor: 'pointer',
                  whiteSpace: 'nowrap', borderRadius: 4, background: t.key === activeTab.key ? '#0d1117' : 'transparent',
                }}>
                <Typography.Text style={{ color: 'inherit', fontSize: 12 }}>
                  {vms[0] || 'Panel nuevo'}{vms.length > 1 ? ` +${vms.length - 1}` : ''}
                </Typography.Text>
                <CloseOutlined style={{ fontSize: 10 }} onClick={(e) => { e.stopPropagation(); closeTab(t.key); }} />
              </div>
            );
          })}
          {/* Pestaña nueva vacía: la VM se elige adentro, igual que en un split. */}
          <Button type="text" size="small" style={ICON_BTN} icon={<PlusOutlined />} aria-label="Nueva pestaña"
            onClick={() => openTerminal(null)} />
        </div>
        {!minimized && (
          <>
            <Tooltip title="Dividir en columnas">
              <Button type="text" size="small" style={ICON_BTN} icon={<SplitIcon dir="h" />}
                onClick={() => splitActive(activeTab.key, 'h')} />
            </Tooltip>
            <Tooltip title="Dividir en filas">
              <Button type="text" size="small" style={ICON_BTN} icon={<SplitIcon dir="v" />}
                onClick={() => splitActive(activeTab.key, 'v')} />
            </Tooltip>
          </>
        )}
        <Tooltip title={minimized ? 'Restaurar' : 'Minimizar'}>
          <Button type="text" size="small" style={ICON_BTN} icon={<MinusOutlined />} onClick={() => setMinimized(!minimized)} />
        </Tooltip>
        {!minimized && (
          <Tooltip title={maximized ? 'Achicar' : 'Agrandar'}>
            <Button type="text" size="small" style={ICON_BTN}
              icon={maximized ? <FullscreenExitOutlined /> : <FullscreenOutlined />} onClick={() => setMaximized(!maximized)} />
          </Tooltip>
        )}
        <Tooltip title="Cerrar todas">
          <Button type="text" size="small" style={ICON_BTN} icon={<CloseOutlined />} onClick={closeAll} />
        </Tooltip>
      </div>
      {/* Minimizado se oculta pero no se desmonta: las sesiones siguen conectadas. */}
      <div style={{ position: 'relative', flex: 1, minHeight: 0, display: minimized ? 'none' : 'block' }}>
        {tabs.map((t) => (
          <TabPanes key={t.key} tab={t} visible={!minimized && t.key === activeTab.key} startDrag={startDrag} />
        ))}
        {dragging && <div style={{ position: 'absolute', inset: 0, zIndex: 5, cursor: dragging }} />}
      </div>
    </div>
  );
}
