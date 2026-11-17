import React, { useCallback, useEffect, useState } from 'react';
import {
  App, Avatar, Button, Col, Divider, Empty, Input, List, Modal, Popconfirm, Row, Select, Space, Spin, Tag,
  Timeline, Tooltip, Typography,
} from 'antd';
import {
  DeleteOutlined, EditOutlined, InboxOutlined, LinkOutlined, PullRequestOutlined, RobotOutlined, UndoOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';

import Markdown from '../Markdown';
import MarkdownEditor from '../MarkdownEditor';
import { cardsApi } from '../../api';
import { extractErrorMessage } from '../../api/axios';
import { useAuth } from '../../contexts/AuthContext';
import { colors, radius } from '../../theme';
import UserAvatar from '../UserAvatar';
import ViaTag from '../ViaTag';
import { describeActivity } from '../../utils/activity';

const SideLabel = ({ children }) => (
  <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', margin: '12px 0 4px' }}>
    {children}
  </Typography.Text>
);

export default function CardModal({ cardId, board, onClose, onChanged }) {
  const { message } = App.useApp();
  const { user, isAdmin } = useAuth();
  const [card, setCard] = useState(null);
  const [editingDesc, setEditingDesc] = useState(false);
  const [desc, setDesc] = useState('');
  const [comment, setComment] = useState('');
  const [prUrl, setPrUrl] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setCard(await cardsApi.get(cardId));
    } catch (err) {
      message.error(extractErrorMessage(err));
      onClose();
    }
  }, [cardId, message, onClose]);

  useEffect(() => { load(); }, [load]);

  const run = async (fn) => {
    setSaving(true);
    try {
      await fn();
      await load();
      onChanged();
      return true;
    } catch (err) {
      message.error(extractErrorMessage(err));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const update = (changes) => run(() => cardsApi.update(cardId, changes));

  if (!card) {
    return <Modal open footer={null} onCancel={onClose} width={900}><Spin /></Modal>;
  }

  const archived = Boolean(card.archivedAt);

  return (
    <Modal
      open
      onCancel={onClose}
      footer={null}
      width={920}
      title={(
        <Space>
          <Typography.Text type="secondary">#{card.id}</Typography.Text>
          {archived && <Tag color="orange">Archivada</Tag>}
        </Space>
      )}
    >
      <Typography.Title
        level={4}
        style={{ marginTop: 0 }}
        editable={{ onChange: (title) => title.trim() && title !== card.title && update({ title }) }}
      >
        {card.title}
      </Typography.Title>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        Creada por {card.createdBy?.name || '—'} · {dayjs(card.createdAt).fromNow()}
      </Typography.Text>

      <Row gutter={24} style={{ marginTop: 12 }}>
        <Col xs={24} md={16}>
          <Space style={{ marginBottom: 6 }}>
            <Typography.Text strong>Descripción</Typography.Text>
            {!editingDesc && (
              <Button size="small" type="text" icon={<EditOutlined />}
                onClick={() => { setDesc(card.description); setEditingDesc(true); }} />
            )}
          </Space>
          {editingDesc ? (
            <>
              <MarkdownEditor autoFocus autoSize={{ minRows: 6, maxRows: 24 }} value={desc}
                onChange={(e) => setDesc(e.target.value)} placeholder="Markdown soportado" />
              <Space style={{ marginTop: 8 }}>
                <Button type="primary" size="small" loading={saving}
                  onClick={async () => { if (await update({ description: desc })) setEditingDesc(false); }}>
                  Guardar
                </Button>
                <Button size="small" onClick={() => setEditingDesc(false)}>Cancelar</Button>
              </Space>
            </>
          ) : card.description ? (
            <div style={{ background: colors.surfaceSubtle, padding: '8px 12px', borderRadius: radius.md }}>
              <Markdown>{card.description}</Markdown>
            </div>
          ) : (
            <Typography.Text type="secondary" italic>Sin descripción</Typography.Text>
          )}

          <Divider orientation="left" plain><PullRequestOutlined /> Pull requests</Divider>
          {card.prUrls.length === 0 && <Typography.Text type="secondary">Sin PRs</Typography.Text>}
          {card.prUrls.map((url) => (
            <div key={url} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <LinkOutlined />
              <a href={url} target="_blank" rel="noreferrer" style={{ flex: 1, wordBreak: 'break-all' }}>{url}</a>
              <Button size="small" type="text" icon={<DeleteOutlined />}
                onClick={() => update({ prUrls: card.prUrls.filter((u) => u !== url) })} />
            </div>
          ))}
          <Space.Compact style={{ width: '100%', marginTop: 8 }}>
            <Input placeholder="https://github.com/.../pull/123" value={prUrl} onChange={(e) => setPrUrl(e.target.value)} />
            <Button disabled={!prUrl.trim()} onClick={async () => {
              if (await update({ prUrls: [...card.prUrls, prUrl.trim()] })) setPrUrl('');
            }}>Agregar</Button>
          </Space.Compact>

          <Divider orientation="left" plain>Comentarios</Divider>
          <MarkdownEditor autoSize={{ minRows: 2, maxRows: 10 }} value={comment} hint={false}
            onChange={(e) => setComment(e.target.value)} placeholder="Escribí un comentario (markdown, podés pegar imágenes)" />
          <Button type="primary" size="small" style={{ marginTop: 6 }} disabled={!comment.trim()} loading={saving}
            onClick={async () => { if (await run(() => cardsApi.addComment(cardId, comment))) setComment(''); }}>
            Comentar
          </Button>
          <List
            style={{ marginTop: 8 }}
            dataSource={[...card.comments].reverse()}
            locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Sin comentarios" /> }}
            renderItem={(c) => (
              <List.Item
                actions={(c.author?.id === user.id || isAdmin) ? [
                  <Popconfirm key="del" title="¿Borrar comentario?"
                    onConfirm={() => run(() => cardsApi.deleteComment(cardId, c.id))}>
                    <Button size="small" type="text" icon={<DeleteOutlined />} />
                  </Popconfirm>,
                ] : []}
              >
                <List.Item.Meta
                  avatar={c.via === 'mcp'
                    ? <Avatar style={{ background: colors.claude }}><RobotOutlined /></Avatar>
                    : <UserAvatar user={c.author} />}
                  title={<span>{c.author?.name}<ViaTag via={c.via} />
                    <Typography.Text type="secondary" style={{ fontSize: 12, marginInlineStart: 8 }}>
                      <Tooltip title={dayjs(c.createdAt).format('YYYY-MM-DD HH:mm')}>{dayjs(c.createdAt).fromNow()}</Tooltip>
                    </Typography.Text>
                  </span>}
                  description={<div style={{ color: colors.text }}><Markdown>{c.body}</Markdown></div>}
                />
              </List.Item>
            )}
          />

          <Divider orientation="left" plain>Historial</Divider>
          <Timeline
            items={card.activity.map((a) => ({
              color: a.via === 'mcp' ? 'purple' : 'blue',
              children: (
                <span style={{ fontSize: 13 }}>
                  <b>{a.actor?.name || '—'}</b> {describeActivity(a)}
                  <ViaTag via={a.via} />
                  <Typography.Text type="secondary" style={{ fontSize: 12, marginInlineStart: 6 }}>
                    {dayjs(a.createdAt).format('YYYY-MM-DD HH:mm')}
                  </Typography.Text>
                </span>
              ),
            }))}
          />
        </Col>

        <Col xs={24} md={8}>
          <SideLabel>Lista</SideLabel>
          <Select
            style={{ width: '100%' }} value={card.listId} disabled={archived}
            options={board.lists.map((l) => ({ value: l.id, label: l.name }))}
            onChange={(listId) => run(() => cardsApi.move(cardId, listId))}
          />
          <SideLabel>Responsable</SideLabel>
          <Select
            allowClear style={{ width: '100%' }} placeholder="Sin asignar" value={card.assignee?.id}
            options={board.users.map((u) => ({ value: u.id, label: u.name }))}
            onChange={(v) => update({ assigneeId: v ?? null })}
          />
          <SideLabel>Repositorios</SideLabel>
          <Select
            mode="multiple" style={{ width: '100%' }} placeholder="Ninguno"
            value={card.repositories.map((r) => r.key)}
            options={board.repositories.map((r) => ({ value: r.key, label: r.key }))}
            onChange={(repositories) => update({ repositories })}
          />
          <Divider />
          {archived ? (
            <Button block icon={<UndoOutlined />} onClick={() => run(() => cardsApi.unarchive(cardId))}>
              Restaurar
            </Button>
          ) : (
            <Popconfirm title="¿Archivar la tarjeta?" onConfirm={async () => {
              if (await run(() => cardsApi.archive(cardId))) onClose();
            }}>
              <Button block danger icon={<InboxOutlined />}>Archivar</Button>
            </Popconfirm>
          )}
        </Col>
      </Row>
    </Modal>
  );
}
