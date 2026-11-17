import React from 'react';
import { useSortable } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Card, Space, Tag, Tooltip, Typography } from 'antd';
import { AlignLeftOutlined, CheckOutlined, CommentOutlined, PullRequestOutlined } from '@ant-design/icons';

import ColorTag from '../ColorTag';
import { colors } from '../../theme';
import UserAvatar from '../UserAvatar';

export function CardBody({ card, dragging = false }) {
  return (
    <Card
      size="small"
      hoverable
      style={{
        boxShadow: dragging ? '0 8px 20px rgba(0,0,0,0.18)' : undefined,
        cursor: 'pointer',
        // Marco verde: el PR ya pasó QA (label "Aprobado"), falta mergearlo.
        ...(card.prApproved ? { borderColor: colors.success, borderInlineStartWidth: 4 } : {}),
      }}
      styles={{ body: { padding: '8px 10px' } }}
    >
      {(card.repositories.length > 0 || card.prApproved) && (
        <div style={{ marginBottom: 4 }}>
          {card.repositories.map((r) => <ColorTag key={`r${r.id}`} color={r.color}>{r.key}</ColorTag>)}
          {card.prApproved && (
            <Tooltip title='El PR tiene el label "Aprobado": listo para Mergear PR'>
              <Tag color="green" icon={<CheckOutlined />} style={{ fontSize: 11 }}>PR aprobado</Tag>
            </Tooltip>
          )}
        </div>
      )}
      <Typography.Text style={{ display: 'block', wordBreak: 'break-word' }}>{card.title}</Typography.Text>
      <div style={{ display: 'flex', alignItems: 'center', marginTop: 6, color: colors.textMuted, fontSize: 12 }}>
        <Space size={10} style={{ flex: 1 }}>
          <span>#{card.id}</span>
          {card.description && <AlignLeftOutlined />}
          {card.commentCount > 0 && <span><CommentOutlined /> {card.commentCount}</span>}
          {card.prUrls.length > 0 && (
            <Tooltip title={card.prUrls.join('\n')}>
              <span style={{ color: colors.claude }}><PullRequestOutlined /> {card.prUrls.length}</span>
            </Tooltip>
          )}
        </Space>
        {card.assignee && (
          <Tooltip title={card.assignee.name}>
            <UserAvatar user={card.assignee} size={22} />
          </Tooltip>
        )}
      </div>
    </Card>
  );
}

export default function CardItem({ card, onOpen, disabled }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `c-${card.id}`,
    disabled,
  });
  const style = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
    marginBottom: 8,
  };
  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners} onClick={() => onOpen(card.id)}>
      <CardBody card={card} />
    </div>
  );
}
