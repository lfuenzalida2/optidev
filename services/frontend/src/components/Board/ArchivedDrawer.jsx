import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { App, Button, Drawer, Empty, List, Typography } from 'antd';
import { UndoOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

import { boardApi, cardsApi } from '../../api';
import { extractErrorMessage } from '../../api/axios';

export default function ArchivedDrawer({ open, onClose, onChanged }) {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [cards, setCards] = useState([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCards(await boardApi.archived());
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { if (open) load(); }, [open, load]);

  const restore = async (id) => {
    try {
      await cardsApi.unarchive(id);
      load();
      onChanged();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  return (
    <Drawer title="Tarjetas archivadas" open={open} onClose={onClose} width={460}>
      <List
        loading={loading}
        dataSource={cards}
        locale={{ emptyText: <Empty description="No hay tarjetas archivadas" /> }}
        renderItem={(card) => (
          <List.Item actions={[
            <Button key="restore" size="small" icon={<UndoOutlined />} onClick={() => restore(card.id)}>Restaurar</Button>,
          ]}>
            <List.Item.Meta
              title={<a onClick={() => { onClose(); navigate(`/cards/${card.id}`); }}>#{card.id} {card.title}</a>}
              description={<Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {card.listName} · archivada {dayjs(card.archivedAt).fromNow()}
              </Typography.Text>}
            />
          </List.Item>
        )}
      />
    </Drawer>
  );
}
