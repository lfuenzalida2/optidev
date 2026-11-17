import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';

import { vmsApi } from '../api';

// Terminales SSH abiertas a las VMs de pruebas. Viven en el Layout: siguen
// conectadas mientras el usuario navega entre páginas.
//
// Cada pestaña tiene un árbol de paneles:
//   hoja  = { type: 'leaf', id, vm, command }   (vm null = panel nuevo, todavía sin VM elegida;
//           command = lo que se escribe en la terminal apenas conecta, p. ej. desde un paso de VmDoctor)
//   split = { type: 'split', id, dir: 'h' | 'v', ratio, first, second }
// 'h' = columnas (izquierda | derecha), 'v' = filas (arriba / abajo).
const TerminalContext = createContext(null);

let lastId = 0;
const newId = () => ++lastId;
const leaf = (vm = null, command = null) => ({ type: 'leaf', id: newId(), vm, command });

const mapTree = (node, fn) => {
  const mapped = fn(node);
  if (mapped !== node || node.type !== 'split') return mapped;
  const first = mapTree(node.first, fn);
  const second = mapTree(node.second, fn);
  return first === node.first && second === node.second ? node : { ...node, first, second };
};

const removeLeaf = (node, id) => {
  if (node.type === 'leaf') return node.id === id ? null : node;
  const first = removeLeaf(node.first, id);
  const second = removeLeaf(node.second, id);
  if (!first) return second;
  if (!second) return first;
  return first === node.first && second === node.second ? node : { ...node, first, second };
};

export const leaves = (node) => (node.type === 'leaf' ? [node] : [...leaves(node.first), ...leaves(node.second)]);

// Rectángulo (en % del contenedor) de cada hoja y de cada divisor. Las hojas se
// dibujan todas en un mismo contenedor con posición absoluta: así dividir no
// cambia dónde está montada cada terminal en React y las sesiones abiertas no
// se reconectan.
export function layoutTree(node, rect = { x: 0, y: 0, w: 100, h: 100 }) {
  if (node.type === 'leaf') return { panes: [{ leaf: node, rect }], dividers: [] };
  const horizontal = node.dir === 'h';
  const firstRect = horizontal
    ? { ...rect, w: rect.w * node.ratio }
    : { ...rect, h: rect.h * node.ratio };
  const secondRect = horizontal
    ? { ...rect, x: rect.x + firstRect.w, w: rect.w - firstRect.w }
    : { ...rect, y: rect.y + firstRect.h, h: rect.h - firstRect.h };
  const a = layoutTree(node.first, firstRect);
  const b = layoutTree(node.second, secondRect);
  const divider = { split: node, parent: rect, at: horizontal ? secondRect.x : secondRect.y };
  return { panes: [...a.panes, ...b.panes], dividers: [divider, ...a.dividers, ...b.dividers] };
}

export function TerminalProvider({ children }) {
  const [tabs, setTabs] = useState([]); // [{ key, tree, activeLeaf }]
  const [activeKey, setActiveKey] = useState(null);
  const [minimized, setMinimized] = useState(false);
  const [vms, setVms] = useState(null); // null = sin cargar

  const loadVms = useCallback(async () => {
    try {
      setVms(await vmsApi.list());
    } catch {
      setVms((prev) => prev || []);
    }
  }, []);

  const updateTab = useCallback((key, fn) => {
    setTabs((prev) => prev.map((t) => (t.key === key ? fn(t) : t)));
  }, []);

  // Se puede abrir más de una sesión a la misma VM (como dos pestañas de terminal).
  // Sin vm, la pestaña arranca vacía y la VM se elige adentro. `command` se
  // escribe (con Enter) al conectar.
  const openTerminal = useCallback((vm = null, command = null) => {
    const first = leaf(vm, command);
    const key = newId();
    setTabs((prev) => [...prev, { key, tree: first, activeLeaf: first.id }]);
    setActiveKey(key);
    setMinimized(false);
  }, []);

  const closeTab = useCallback((key) => {
    setTabs((prev) => prev.filter((t) => t.key !== key));
    setActiveKey((active) => {
      if (active !== key) return active;
      const rest = tabs.filter((t) => t.key !== key);
      return rest[rest.length - 1]?.key ?? null;
    });
  }, [tabs]);

  const closeAll = useCallback(() => {
    setTabs([]);
    setActiveKey(null);
  }, []);

  // Divide el panel activo de la pestaña: el nuevo queda a la derecha/abajo,
  // sin VM, para elegirla ahí mismo.
  const splitActive = useCallback((key, dir) => {
    updateTab(key, (tab) => {
      const added = leaf();
      const tree = mapTree(tab.tree, (n) => (n.type === 'leaf' && n.id === tab.activeLeaf
        ? { type: 'split', id: newId(), dir, ratio: 0.5, first: n, second: added }
        : n));
      return { ...tab, tree, activeLeaf: added.id };
    });
  }, [updateTab]);

  const closePane = useCallback((key, leafId) => {
    const tab = tabs.find((t) => t.key === key);
    if (!tab) return;
    const tree = removeLeaf(tab.tree, leafId);
    if (!tree) {
      closeTab(key);
      return;
    }
    const active = tab.activeLeaf === leafId ? leaves(tree)[0].id : tab.activeLeaf;
    updateTab(key, (t) => ({ ...t, tree, activeLeaf: active }));
  }, [tabs, closeTab, updateTab]);

  const setPaneVm = useCallback((key, leafId, vm) => {
    updateTab(key, (tab) => ({
      ...tab, tree: mapTree(tab.tree, (n) => (n.type === 'leaf' && n.id === leafId ? { ...n, vm } : n)),
    }));
  }, [updateTab]);

  const setActiveLeaf = useCallback((key, leafId) => {
    updateTab(key, (tab) => (tab.activeLeaf === leafId ? tab : { ...tab, activeLeaf: leafId }));
  }, [updateTab]);

  const setRatio = useCallback((key, splitId, ratio) => {
    updateTab(key, (tab) => ({
      ...tab, tree: mapTree(tab.tree, (n) => (n.type === 'split' && n.id === splitId ? { ...n, ratio } : n)),
    }));
  }, [updateTab]);

  const value = useMemo(() => ({
    tabs, activeKey, setActiveKey, minimized, setMinimized, vms, loadVms,
    openTerminal, closeTab, closeAll, splitActive, closePane, setPaneVm, setActiveLeaf, setRatio,
  }), [tabs, activeKey, minimized, vms, loadVms, openTerminal, closeTab, closeAll, splitActive, closePane,
    setPaneVm, setActiveLeaf, setRatio]);

  return <TerminalContext.Provider value={value}>{children}</TerminalContext.Provider>;
}

export const useTerminals = () => useContext(TerminalContext);
