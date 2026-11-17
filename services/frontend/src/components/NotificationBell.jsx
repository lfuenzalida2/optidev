import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Button, Empty, List, Popover, Typography } from 'antd';
import { BellOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useNavigate } from 'react-router-dom';

import { notificationsApi } from '../api';
import { colors } from '../theme';

const POLL_MS = 60 * 1000;

// Por tipo de aviso: el texto y a dónde lleva al tocarlo.
const KINDS = {
  vm_requested: { text: (d) => `${d.name} (${d.username}) solicitó una VM de pruebas`, to: '/vms' },
  vm_assigned: { text: (d) => `Un admin te asignó la VM «${d.vmName}»`, to: '/account?seccion=vm' },
  repo_out_of_standard: {
    text: (d) => {
      const problems = [d.failing.length && `${d.failing.length} en falla`, d.unknown.length && `${d.unknown.length} sin verificar`];
      return `El repositorio ${d.key} no cumple el estándar (${problems.filter(Boolean).join(', ')})`;
    },
    to: '/settings?seccion=repositorios',
  },
};

const describe = (n) => (KINDS[n.kind] ? KINDS[n.kind].text(n.data) : n.kind);

export default function NotificationBell() {
  const navigate = useNavigate();
  const [data, setData] = useState({ items: [], unreadCount: 0 });

  const load = useCallback(() => notificationsApi.list().then(setData).catch(() => {}), []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  const open = (n) => {
    if (!n.readAt) notificationsApi.read(n.id).then(load).catch(() => {});
    if (KINDS[n.kind]) navigate(KINDS[n.kind].to);
  };
  const readAll = () => notificationsApi.readAll().then(load).catch(() => {});

  const content = (
    <div style={{ width: 340 }}>
      {data.items.length ? (
        <List
          size="small" style={{ maxHeight: 360, overflowY: 'auto' }} dataSource={data.items}
          renderItem={(n) => (
            <List.Item onClick={() => open(n)} style={{ cursor: 'pointer', opacity: n.readAt ? 0.6 : 1 }}>
              <List.Item.Meta
                title={<span style={{ fontWeight: n.readAt ? 400 : 600 }}>{describe(n)}</span>}
                description={dayjs(n.createdAt).fromNow()}
              />
            </List.Item>
          )}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Sin notificaciones" />
      )}
      {data.unreadCount > 0 && (
        <Typography.Link onClick={readAll} style={{ display: 'block', textAlign: 'right', marginTop: 8 }}>
          Marcar todas como leídas
        </Typography.Link>
      )}
    </div>
  );

  return (
    <Popover content={content} title="Notificaciones" trigger="click" placement="bottomRight">
      <Badge count={data.unreadCount} size="small" offset={[-4, 4]}>
        <Button type="text" icon={<BellOutlined />} style={{ color: colors.onDark }} aria-label="Notificaciones" />
      </Badge>
    </Popover>
  );
}
