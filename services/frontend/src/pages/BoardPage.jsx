import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  closestCorners, DndContext, DragOverlay, KeyboardSensor, PointerSensor, useSensor, useSensors,
} from '@dnd-kit/core';
import { arrayMove, sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { App, Button, Input, Select, Space, Spin, Tooltip } from 'antd';
import { InboxOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';

import Column from '../components/Board/Column';
import { CardBody } from '../components/Board/CardItem';
import CardModal from '../components/Board/CardModal';
import ArchivedDrawer from '../components/Board/ArchivedDrawer';
import { boardApi, cardsApi, githubApi, listsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { colors } from '../theme';
import { useAuth } from '../contexts/AuthContext';
import { hasApprovedPull } from '../utils/approval';
import { dropPosition, placeAt } from '../utils/dropPosition';
import { readExpandedLists, writeExpandedLists } from '../utils/expandedLists';

// El tablero se refresca solo para ver lo que mueven otras personas o Claude
// (vía MCP). No se refresca mientras se arrastra una tarjeta.
const POLL_MS = 15000;

const parseKey = (key) => {
  const [kind, id] = String(key).split('-');
  return { kind, id: Number(id) };
};

export default function BoardPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { cardId } = useParams();
  const { user } = useAuth();
  const [board, setBoard] = useState(null);
  // Orden de tarjetas por lista: { [listId]: [cardId, ...] }. Es la copia
  // local que se muta durante el drag para previsualizar el resultado.
  const [order, setOrder] = useState({});
  const [activeId, setActiveId] = useState(null);
  // El tablero abre filtrado por las tarjetas del usuario; se quita con el
  // filtro "Responsable".
  const [filters, setFilters] = useState({ text: '', repository: null, assignee: user?.id ?? null });
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [newListName, setNewListName] = useState(null);
  const draggingRef = useRef(false);
  // Listas ocultables (collapsible) que este usuario expandió; el resto se ve colapsado.
  const [expandedLists, setExpandedLists] = useState(() => readExpandedLists(user?.id));

  useEffect(() => { setExpandedLists(readExpandedLists(user?.id)); }, [user?.id]);

  const toggleExpanded = (listId) => {
    setExpandedLists((prev) => {
      const next = new Set(prev);
      if (next.has(listId)) next.delete(listId);
      else next.add(listId);
      writeExpandedLists(user?.id, next);
      return next;
    });
  };

  const load = useCallback(async () => {
    try {
      const data = await boardApi.get();
      if (draggingRef.current) return;
      setBoard(data);
      setOrder(Object.fromEntries(data.lists.map((l) => [l.id, l.cards.map((c) => c.id)])));
    } catch (err) {
      if (err.response?.status !== 401) message.error(extractErrorMessage(err));
    }
  }, [message]);

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') load();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  // Tarjetas con algún PR aprobado en GitHub (label "Aprobado"). Es un extra
  // visual: sin GITHUB_TOKEN o con GitHub caído el tablero se ve igual, sin la marca.
  const [approvedIds, setApprovedIds] = useState(() => new Set());
  const loadApproved = useCallback(async () => {
    try {
      const { cards } = await githubApi.testing();
      setApprovedIds(new Set(cards.filter(hasApprovedPull).map((c) => c.id)));
    } catch {
      setApprovedIds(new Set());
    }
  }, []);

  useEffect(() => {
    loadApproved();
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') loadApproved();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [loadApproved]);

  const cardsById = useMemo(() => {
    const map = {};
    board?.lists.forEach((l) => l.cards.forEach((c) => { map[c.id] = { ...c, prApproved: approvedIds.has(c.id) }; }));
    return map;
  }, [board, approvedIds]);

  // Búsqueda explícita (texto o repositorio): revela temporalmente las
  // listas ocultas con resultados y bloquea el arrastre. El filtro de
  // responsable no cuenta: el tablero abre con él (la posición del drop se
  // ancla a las tarjetas visibles, ver utils/dropPosition.js) y no debe abrir
  // Backlog cada vez.
  const searchActive = Boolean(filters.text || filters.repository);
  const dragBlocked = searchActive;
  const matches = useCallback((card) => {
    if (filters.repository && !card.repositories.some((r) => r.key === filters.repository)) return false;
    if (filters.assignee === 'none' && card.assignee) return false;
    if (filters.assignee && filters.assignee !== 'none' && card.assignee?.id !== filters.assignee) return false;
    if (filters.text) {
      const q = filters.text.toLowerCase();
      const hay = `#${card.id} ${card.title} ${card.description}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }, [filters]);

  const sensors = useSensors(
    // Con distancia mínima un click abre la tarjeta y no inicia un drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const findList = (key) => {
    const { kind, id } = parseKey(key);
    if (kind === 'l') return id;
    return Number(Object.keys(order).find((listId) => order[listId].includes(id)));
  };

  const onDragStart = ({ active }) => {
    draggingRef.current = true;
    setActiveId(parseKey(active.id).id);
  };

  const onDragOver = ({ active, over }) => {
    if (!over) return;
    const from = findList(active.id);
    const to = findList(over.id);
    if (!from || !to || from === to) return;
    const cardIdNum = parseKey(active.id).id;
    setOrder((prev) => {
      const target = prev[to].filter((id) => id !== cardIdNum);
      const overInfo = parseKey(over.id);
      const index = overInfo.kind === 'c' ? target.indexOf(overInfo.id) : target.length;
      target.splice(index < 0 ? target.length : index, 0, cardIdNum);
      return { ...prev, [from]: prev[from].filter((id) => id !== cardIdNum), [to]: target };
    });
  };

  const onDragEnd = async ({ active, over }) => {
    const cardIdNum = parseKey(active.id).id;
    setActiveId(null);
    if (!over) {
      draggingRef.current = false;
      return load();
    }
    const listId = findList(over.id);
    let ids = order[listId];
    const overInfo = parseKey(over.id);
    if (overInfo.kind === 'c' && overInfo.id !== cardIdNum) {
      ids = arrayMove(ids, ids.indexOf(cardIdNum), ids.indexOf(overInfo.id));
      setOrder((prev) => ({ ...prev, [listId]: ids }));
    }
    // Con tarjetas ocultas por el filtro, la posición se calcula respecto de
    // los vecinos visibles en el orden completo. En una lista colapsada no se
    // ve ninguna tarjeta: la que se suelta ahí queda al final.
    const targetList = board.lists.find((l) => l.id === listId);
    const collapsedTarget = Boolean(targetList?.collapsible && !expandedLists.has(listId));
    const position = dropPosition(
      ids, cardIdNum, (id) => collapsedTarget || (cardsById[id] && matches(cardsById[id])),
    );
    if (position !== ids.indexOf(cardIdNum)) {
      ids = placeAt(ids, cardIdNum, position);
      setOrder((prev) => ({ ...prev, [listId]: ids }));
    }
    const original = cardsById[cardIdNum];
    draggingRef.current = false;
    if (original.listId === listId && original.position === position) return;
    try {
      await cardsApi.move(cardIdNum, listId, position);
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
    load();
  };

  const onDragCancel = () => {
    draggingRef.current = false;
    setActiveId(null);
    load();
  };

  const createList = async () => {
    const name = (newListName || '').trim();
    setNewListName(null);
    if (!name) return;
    try {
      await listsApi.create(name);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  if (!board) return <Spin style={{ marginTop: 80 }} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <Space wrap style={{ padding: '12px 16px' }}>
        <Input.Search
          allowClear placeholder="Buscar (#id, título, descripción)" style={{ width: 260 }}
          onChange={(e) => setFilters((f) => ({ ...f, text: e.target.value }))}
        />
        <Select
          allowClear placeholder="Repositorio" style={{ width: 170 }} value={filters.repository}
          onChange={(v) => setFilters((f) => ({ ...f, repository: v ?? null }))}
          options={board.repositories.map((r) => ({ value: r.key, label: r.key }))}
        />
        <Select
          allowClear placeholder="Responsable" style={{ width: 170 }} value={filters.assignee}
          onChange={(v) => setFilters((f) => ({ ...f, assignee: v ?? null }))}
          options={[{ value: 'none', label: 'Sin asignar' }, ...board.users.map((u) => ({ value: u.id, label: u.name }))]}
        />
        {dragBlocked && (
          <span style={{ color: colors.textMuted, fontSize: 12 }}>
            Con búsqueda o repositorio no se puede arrastrar
          </span>
        )}
        <Tooltip title="Recargar"><Button icon={<ReloadOutlined />} onClick={load} /></Tooltip>
        <Button icon={<InboxOutlined />} onClick={() => setArchivedOpen(true)}>Archivadas</Button>
      </Space>

      <div style={{ flex: 1, minHeight: 0, overflowX: 'auto', padding: '0 16px 16px' }}>
        <DndContext
          sensors={sensors}
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragOver={onDragOver}
          onDragEnd={onDragEnd}
          onDragCancel={onDragCancel}
        >
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', height: '100%' }}>
            {board.lists.map((list) => {
              const cards = (order[list.id] || []).map((id) => cardsById[id]).filter((c) => c && matches(c));
              // Ocultar es solo visual: con una búsqueda activa, una lista oculta
              // que tiene resultados se muestra para no esconderlos.
              const hidden = list.collapsible && !expandedLists.has(list.id);
              const shownByFilter = hidden && searchActive && cards.length > 0;
              return (
                <Column
                  key={list.id}
                  list={list}
                  cards={cards}
                  repositories={board.repositories}
                  onOpen={(id) => navigate(`/cards/${id}`)}
                  onChanged={load}
                  dragDisabled={dragBlocked}
                  collapsed={hidden && !shownByFilter}
                  onToggleCollapsed={shownByFilter ? null : () => toggleExpanded(list.id)}
                />
              );
            })}
            <div style={{ flex: '0 0 270px' }}>
              {newListName === null ? (
                <Button icon={<PlusOutlined />} block onClick={() => setNewListName('')}>Agregar lista</Button>
              ) : (
                <Input
                  autoFocus placeholder="Nombre de la lista" value={newListName}
                  onChange={(e) => setNewListName(e.target.value)} onPressEnter={createList} onBlur={createList}
                />
              )}
            </div>
          </div>
          <DragOverlay>
            {activeId && cardsById[activeId] ? <CardBody card={cardsById[activeId]} dragging /> : null}
          </DragOverlay>
        </DndContext>
      </div>

      {cardId && (
        <CardModal
          cardId={Number(cardId)}
          board={board}
          onClose={() => navigate('/')}
          onChanged={load}
        />
      )}
      <ArchivedDrawer open={archivedOpen} onClose={() => setArchivedOpen(false)} onChanged={load} />
    </div>
  );
}
