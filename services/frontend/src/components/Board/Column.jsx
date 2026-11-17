import React, { useState } from 'react';
import { useDroppable } from '@dnd-kit/core';
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { App, Badge, Button, Dropdown, Input, Space, Tooltip, Typography } from 'antd';
import {
  BgColorsOutlined, DeleteOutlined, EditOutlined, EyeInvisibleOutlined, EyeOutlined, MenuFoldOutlined,
  MenuUnfoldOutlined, MoreOutlined, PlusOutlined,
} from '@ant-design/icons';

import CardItem from './CardItem';
import NewCardModal from './NewCardModal';
import { listsApi } from '../../api';
import { extractErrorMessage } from '../../api/axios';
import { useAuth } from '../../contexts/AuthContext';
import { colors, LIST_COLORS, radius } from '../../theme';

// Solo se pueden crear tarjetas desde la web en estas listas (por nombre, sin distinguir mayúsculas).
const CARD_CREATION_LISTS = ['pendiente', 'en proceso'];

export default function Column({
  list, cards, repositories, onOpen, onChanged, dragDisabled, collapsed = false, onToggleCollapsed = null,
}) {
  const { message, modal } = App.useApp();
  const { isAdmin } = useAuth();
  const { setNodeRef, isOver } = useDroppable({ id: `l-${list.id}` });
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(list.name);
  const canAddCards = CARD_CREATION_LISTS.includes(list.name.trim().toLowerCase());

  const rename = async () => {
    setRenaming(false);
    const value = name.trim();
    if (!value || value === list.name) return setName(list.name);
    try {
      await listsApi.rename(list.id, value);
      onChanged();
    } catch (err) {
      message.error(extractErrorMessage(err));
      setName(list.name);
    }
  };

  const remove = () => modal.confirm({
    title: `¿Eliminar la lista "${list.name}"?`,
    content: 'Solo se puede eliminar si no tiene tarjetas activas.',
    okType: 'danger',
    okText: 'Eliminar',
    onOk: async () => {
      try {
        await listsApi.remove(list.id);
        onChanged();
      } catch (err) {
        message.error(extractErrorMessage(err));
      }
    },
  });

  // Marca la lista como ocultable para todos (p. ej. Backlog). Cada usuario
  // decide después si la expande; por defecto se ve colapsada.
  const toggleCollapsible = async () => {
    try {
      await listsApi.setCollapsible(list.id, !list.collapsible);
      onChanged();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const setColor = async (color) => {
    try {
      await listsApi.setColor(list.id, color);
      onChanged();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const swatch = (color) => (
    <span style={{
      display: 'inline-block', width: 14, height: 14, borderRadius: radius.sm, verticalAlign: 'middle',
      background: color || colors.column, border: `1px solid ${colors.border}`, marginRight: 8,
    }} />
  );

  const menu = {
    items: [
      { key: 'rename', icon: <EditOutlined />, label: 'Renombrar' },
      list.collapsible
        ? { key: 'collapsible', icon: <EyeOutlined />, label: 'No permitir ocultar' }
        : { key: 'collapsible', icon: <EyeInvisibleOutlined />, label: 'Permitir ocultar (oculta por defecto)' },
      // El color de las listas lo define un admin (es parte del diseño del tablero, igual para todos).
      ...(isAdmin ? [{
        key: 'color', icon: <BgColorsOutlined />, label: 'Color',
        children: [
          { key: 'color:', label: <>{swatch(null)}Sin color</> },
          ...LIST_COLORS.map((c) => ({ key: `color:${c}`, label: <>{swatch(c)}{c}</> })),
        ],
      }] : []),
      { key: 'delete', icon: <DeleteOutlined />, label: 'Eliminar', danger: true },
    ],
    onClick: ({ key }) => {
      if (key === 'rename') setRenaming(true);
      else if (key === 'collapsible') toggleCollapsible();
      else if (key === 'delete') remove();
      else if (key.startsWith('color:')) setColor(key.slice(6) || null);
    },
  };

  if (collapsed) {
    // Franja angosta: sigue siendo zona de drop (la tarjeta queda al final).
    return (
      <div ref={setNodeRef} style={{
        width: 44, flex: '0 0 44px', background: isOver ? colors.columnDropTarget : colors.column,
        borderRadius: radius.lg, borderTop: list.color ? `4px solid ${list.color}` : undefined,
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '8px 0',
        maxHeight: '100%', cursor: 'pointer',
      }} onClick={onToggleCollapsed ?? undefined}>
        <Tooltip title={`Mostrar "${list.name}"`} placement="right">
          <Button type="text" size="small" icon={<MenuUnfoldOutlined />} aria-label={`Mostrar ${list.name}`} />
        </Tooltip>
        <Badge count={cards.length} showZero color={list.color || colors.badgeNeutral} />
        <Typography.Text strong style={{ writingMode: 'vertical-rl', whiteSpace: 'nowrap' }}>
          {list.name}
        </Typography.Text>
      </div>
    );
  }

  return (
    <div style={{
      width: 290, flex: '0 0 290px', background: isOver ? colors.columnDropTarget : colors.column,
      borderRadius: radius.lg, borderTop: list.color ? `4px solid ${list.color}` : undefined,
      display: 'flex', flexDirection: 'column', maxHeight: '100%',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', padding: '10px 10px 6px' }}>
        {renaming ? (
          <Input size="small" autoFocus value={name} onChange={(e) => setName(e.target.value)}
            onBlur={rename} onPressEnter={rename} />
        ) : (
          <Space style={{ flex: 1 }}>
            <Typography.Text strong>{list.name}</Typography.Text>
            <Badge count={cards.length} showZero color={list.color || colors.badgeNeutral} />
          </Space>
        )}
        {list.collapsible && onToggleCollapsed && (
          <Tooltip title="Ocultar lista">
            <Button type="text" size="small" icon={<MenuFoldOutlined />} onClick={onToggleCollapsed}
              aria-label={`Ocultar ${list.name}`} />
          </Tooltip>
        )}
        <Dropdown menu={menu} trigger={['click']}>
          <Button type="text" size="small" icon={<MoreOutlined />} />
        </Dropdown>
      </div>
      <div ref={setNodeRef} style={{ padding: '0 8px', overflowY: 'auto', flex: 1, minHeight: 40 }}>
        <SortableContext items={cards.map((c) => `c-${c.id}`)} strategy={verticalListSortingStrategy}>
          {cards.map((card) => (
            <CardItem key={card.id} card={card} onOpen={onOpen} disabled={dragDisabled} />
          ))}
        </SortableContext>
      </div>
      {canAddCards && <div style={{ padding: 8 }}>
        <Button type="text" block icon={<PlusOutlined />} style={{ textAlign: 'left' }} onClick={() => setAdding(true)}>
          Agregar tarjeta
        </Button>
      </div>}
      {adding && (
        <NewCardModal
          list={list}
          repositories={repositories}
          onClose={() => setAdding(false)}
          onCreated={() => { setAdding(false); onChanged(); }}
        />
      )}
    </div>
  );
}
