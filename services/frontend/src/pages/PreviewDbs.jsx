import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, App, Button, Card, Drawer, Empty, Input, Modal, Space, Table, Tabs, Tag, Tooltip, Typography,
} from 'antd';
import { DatabaseOutlined, DeleteOutlined, EyeOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons';

import { useAuth } from '../contexts/AuthContext';
import { previewDbsApi } from '../api';
import { extractErrorMessage } from '../api/axios';

const PAGE_SIZE = 50;

const formatBytes = (bytes) => {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
};

const cellText = (value) => {
  if (value === null || value === undefined) return <Typography.Text type="secondary" italic>NULL</Typography.Text>;
  return <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{String(value)}</span>;
};

// Filas de una tabla, paginadas en el servidor (sin total: se pide una fila de
// más para saber si hay otra página).
function RowsView({ username, table }) {
  const { message } = App.useApp();
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (p) => {
    setLoading(true);
    try {
      setData(await previewDbsApi.rows(username, table, p, PAGE_SIZE));
      setPage(p);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [username, table, message]);

  useEffect(() => { setData(null); load(1); }, [load]);

  const columns = (data?.columns || []).map((name, i) => ({
    title: name, key: `${i}`, dataIndex: i, ellipsis: false, width: 180, render: cellText,
  }));
  const rows = (data?.rows || []).map((row, i) => ({ ...row, rowKey: i }));

  return (
    <>
      <Space style={{ marginBottom: 8 }} wrap>
        <Button size="small" disabled={page <= 1 || loading} onClick={() => load(page - 1)}>Anterior</Button>
        <Typography.Text>Página {page}</Typography.Text>
        <Button size="small" disabled={!data?.hasMore || loading} onClick={() => load(page + 1)}>Siguiente</Button>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => load(page)} />
        <Typography.Text type="secondary">
          {PAGE_SIZE} filas por página{data?.orderBy?.length ? `, ordenadas por ${data.orderBy.join(', ')}` : ''}.
          Solo lectura; los textos largos y binarios se muestran recortados.
        </Typography.Text>
      </Space>
      <Table size="small" rowKey="rowKey" columns={columns} dataSource={rows} loading={loading}
        pagination={false} scroll={{ x: 'max-content', y: 'calc(100vh - 320px)' }} bordered />
    </>
  );
}

function ColumnsView({ username, table }) {
  const { message } = App.useApp();
  const [columns, setColumns] = useState(null);

  useEffect(() => {
    setColumns(null);
    previewDbsApi.columns(username, table).then((d) => setColumns(d.columns))
      .catch((err) => message.error(extractErrorMessage(err)));
  }, [username, table, message]);

  return (
    <Table size="small" rowKey="name" loading={!columns} dataSource={columns || []} pagination={false}
      columns={[
        { title: 'Columna', dataIndex: 'name', render: (n) => <Typography.Text code>{n}</Typography.Text> },
        { title: 'Tipo', dataIndex: 'type' },
        { title: 'Nulo', dataIndex: 'nullable', render: (v) => (v ? 'Sí' : 'No') },
        { title: 'Clave', dataIndex: 'key', render: (k) => k && <Tag>{k}</Tag> },
        { title: 'Por defecto', dataIndex: 'default', render: cellText },
        { title: 'Extra', dataIndex: 'extra' },
      ]} />
  );
}

// Explorador de una base: tablas a la izquierda, datos y columnas a la derecha.
function Explorer({ target, onClose }) {
  const { message } = App.useApp();
  const [tables, setTables] = useState(null);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    if (!target) return;
    setTables(null);
    setSelected(null);
    setFilter('');
    previewDbsApi.tables(target.username).then((d) => setTables(d.tables))
      .catch((err) => {
        message.error(extractErrorMessage(err));
        setTables([]);
      });
  }, [target, message]);

  const visible = (tables || []).filter((t) => t.name.toLowerCase().includes(filter.trim().toLowerCase()));

  return (
    <Drawer open={!!target} onClose={onClose} width="92%" destroyOnClose
      title={target && <Space><DatabaseOutlined />{target.database}<Typography.Text type="secondary">({target.name})</Typography.Text></Space>}>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <div style={{ width: 280, flexShrink: 0 }}>
          <Input.Search allowClear size="small" placeholder="Filtrar tablas" style={{ marginBottom: 8 }}
            onChange={(e) => setFilter(e.target.value)} />
          <Table size="small" rowKey="name" loading={!tables} dataSource={visible} pagination={false}
            scroll={{ y: 'calc(100vh - 220px)' }}
            locale={{ emptyText: 'La base no tiene tablas' }}
            onRow={(t) => ({
              onClick: () => setSelected(t.name),
              style: { cursor: 'pointer', fontWeight: t.name === selected ? 600 : undefined },
            })}
            columns={[
              {
                title: `Tablas (${tables?.length ?? 0})`, dataIndex: 'name',
                render: (n, t) => <Space size={4}>{n}{t.type === 'view' && <Tag>vista</Tag>}</Space>,
              },
              {
                title: 'Filas', dataIndex: 'rowsEstimate', width: 80, align: 'right',
                render: (v) => <Tooltip title="Estimado por MySQL">{v ?? '—'}</Tooltip>,
              },
            ]} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          {selected ? (
            <Tabs key={selected} items={[
              { key: 'rows', label: 'Datos', children: <RowsView username={target.username} table={selected} /> },
              { key: 'columns', label: 'Columnas', children: <ColumnsView username={target.username} table={selected} /> },
            ]} tabBarExtraContent={<Typography.Text code>{selected}</Typography.Text>} />
          ) : <Empty description="Elegí una tabla" style={{ marginTop: 64 }} />}
        </div>
      </div>
    </Drawer>
  );
}

export default function PreviewDbs() {
  const { message } = App.useApp();
  const { isAdmin } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [exploring, setExploring] = useState(null);
  const [resetting, setResetting] = useState(null);
  const [confirmText, setConfirmText] = useState('');
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await previewDbsApi.list());
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const prepare = async (row) => {
    setBusy(row.username);
    try {
      const r = await previewDbsApi.prepare(row.username);
      message.success(`Base ${r.database} lista`);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    const row = resetting;
    setBusy(row.username);
    try {
      const r = await previewDbsApi.reset(row.username, confirmText);
      message.success(`Base ${r.database} recreada vacía`);
      setResetting(null);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setBusy(null);
    }
  };

  const status = (row) => {
    if (row.exists === null) return <Tag>Desconocido</Tag>;
    return row.exists ? <Tag color="green">Creada</Tag> : <Tag>No creada</Tag>;
  };

  const columns = [
    {
      title: 'Usuario', dataIndex: 'username',
      render: (u, row) => <Space size={6}>{row.name}<Typography.Text type="secondary">@{u}</Typography.Text>{row.mine && <Tag color="blue">tuya</Tag>}</Space>,
    },
    { title: 'Base', dataIndex: 'database', render: (d) => <Typography.Text code>{d}</Typography.Text> },
    {
      title: 'Estado', key: 'status',
      render: (_, row) => <Space size={4}>{status(row)}{row.loadRunning && <Tag color="processing">Carga en curso</Tag>}</Space>,
    },
    { title: 'Tablas', dataIndex: 'tables', align: 'right', render: (v) => v ?? '—' },
    {
      title: <Tooltip title="Estimado por MySQL (datos + índices); puede tener retraso">Tamaño</Tooltip>,
      dataIndex: 'sizeBytes', align: 'right', render: formatBytes,
    },
    {
      title: <Tooltip title="Estimado por MySQL">Filas aprox.</Tooltip>,
      dataIndex: 'rowsEstimate', align: 'right', render: (v) => (v == null ? '—' : v.toLocaleString('es-CL')),
    },
    {
      title: '', key: 'actions', align: 'right',
      render: (_, row) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} disabled={!row.exists} onClick={() => setExploring(row)}>Explorar</Button>
          {row.exists === false && (
            <Button size="small" icon={<ToolOutlined />} loading={busy === row.username} onClick={() => prepare(row)}>
              Crear
            </Button>
          )}
          {row.exists && (
            <Button size="small" danger icon={<DeleteOutlined />} disabled={row.loadRunning}
              onClick={() => { setConfirmText(''); setResetting(row); }}>
              Recrear vacía
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Card
        title={<Space><DatabaseOutlined />Bases de preview</Space>}
        extra={(
          <Space>
            {data?.mysql && <Typography.Text type="secondary">MySQL central: {data.mysql.host}:{data.mysql.port}</Typography.Text>}
            <Button icon={<ReloadOutlined />} loading={loading} onClick={load}>Actualizar</Button>
          </Space>
        )}
      >
        <Typography.Paragraph type="secondary">
          Cada usuario de optidev tiene su base <Typography.Text code>db_&lt;usuario&gt;</Typography.Text> en el MySQL
          central de preview: es la que usan los servicios cargados en su VM de pruebas.
          {isAdmin ? ' Como admin ves las de todos.' : ' Acá ves la tuya.'} La exploración es de solo lectura.
        </Typography.Paragraph>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        {data?.error && (
          <Alert type="warning" showIcon style={{ marginBottom: 16 }}
            message="No se pudo consultar el MySQL central" description={data.error} />
        )}
        <Table rowKey="username" size="middle" loading={loading} columns={columns}
          dataSource={data?.databases || []} pagination={false} />
      </Card>

      <Explorer target={exploring} onClose={() => setExploring(null)} />

      <Modal open={!!resetting} title={`Recrear ${resetting?.database} vacía`} okText="Borrar y recrear"
        okButtonProps={{ danger: true, disabled: confirmText !== resetting?.database, loading: !!busy }}
        onOk={reset} onCancel={() => setResetting(null)} destroyOnClose>
        <Typography.Paragraph>
          Se <b>borran todas las tablas y datos</b> de <Typography.Text code>{resetting?.database}</Typography.Text> y
          la base se crea de nuevo vacía, con el mismo usuario y contraseña de MySQL (los <Typography.Text code>.env</Typography.Text> de
          la VM siguen sirviendo). No se puede deshacer: los datos cargados (p. ej. un dump) hay que volver a cargarlos.
        </Typography.Paragraph>
        <Typography.Paragraph>Para confirmar, escribí el nombre de la base:</Typography.Paragraph>
        <Input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder={resetting?.database}
          onPressEnter={() => confirmText === resetting?.database && reset()} />
      </Modal>
    </div>
  );
}
