import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, App, Avatar, Badge, Button, Card, Input, Space, Table, Tag, Tooltip, Typography } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

import ColorTag from '../components/ColorTag';
import GithubChecks from '../components/GithubChecks';
import { githubApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { useAuth } from '../contexts/AuthContext';
import { textOn } from '../utils/colors';

export default function GithubDashboard() {
  const { message } = App.useApp();
  const { user } = useAuth();
  // Los logins de GitHub no distinguen mayúsculas: se compara en minúsculas.
  const myLogin = user?.githubLogin?.toLowerCase();
  // Arranca filtrada por el usuario de la sesión (si tiene su cuenta de GitHub
  // vinculada); se quita desde el filtro de la columna Autor.
  const [authorFilter, setAuthorFilter] = useState(myLogin ? [myLogin] : null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(null);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      setData(await githubApi.pulls(refresh));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
      if (refresh) message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const createCard = async (pr) => {
    setCreating(pr.rowKey);
    try {
      const card = await githubApi.createCardFromPull(pr.repo.key, pr.number);
      message.success(`Tarjeta #${card.id} creada en PR`);
      await load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setCreating(null);
    }
  };

  const repos = data?.repositories || [];
  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return repos.flatMap((repo) => repo.pulls.map((pr) => ({ ...pr, repo, rowKey: `${repo.key}#${pr.number}` })))
      .filter((pr) => !q || [pr.title, pr.author.login, pr.headRef, pr.baseRef, pr.repo.key, `#${pr.number}`]
        .some((v) => v.toLowerCase().includes(q)))
      .filter((pr) => !authorFilter?.length || authorFilter.includes(pr.author.login.toLowerCase()));
  }, [repos, search, authorFilter]);

  const authors = [...new Set(repos.flatMap((r) => r.pulls.map((pr) => pr.author.login)))]
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));

  const columns = [
    {
      title: 'Repo', dataIndex: ['repo', 'key'], width: 150,
      filters: repos.filter((r) => r.pulls.length).map((r) => ({ text: r.key, value: r.key })),
      onFilter: (value, pr) => pr.repo.key === value,
      render: (key, pr) => <ColorTag color={pr.repo.color}>{key}</ColorTag>,
    },
    {
      title: 'Pull request', dataIndex: 'title',
      render: (title, pr) => (
        <Space size={6} wrap>
          <Typography.Text type="secondary">#{pr.number}</Typography.Text>
          <a href={pr.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{title}</a>
          {pr.isDraft && <Tag>Borrador</Tag>}
        </Space>
      ),
    },
    {
      title: 'Ramas', key: 'refs',
      render: (_, pr) => (
        <Typography.Text code style={{ whiteSpace: 'nowrap' }}>{pr.headRef} → {pr.baseRef}</Typography.Text>
      ),
    },
    {
      title: 'Autor', key: 'author', dataIndex: ['author', 'login'], width: 160,
      filters: authors.map((login) => ({ text: login, value: login.toLowerCase() })),
      filteredValue: authorFilter,
      render: (login, pr) => <Space size={6}><Avatar size={20} src={pr.author.avatarUrl}>{login[0]}</Avatar>{login}</Space>,
    },
    {
      title: 'Etiquetas', dataIndex: 'labels',
      render: (labels) => labels.map((l) => (
        <Tag key={l.name} color={l.color} style={{ color: textOn(l.color), marginInlineEnd: 4 }}>{l.name}</Tag>
      )),
    },
    {
      title: 'Abierto', dataIndex: 'createdAt', width: 130,
      defaultSortOrder: 'descend',
      sorter: (a, b) => dayjs(b.createdAt).diff(dayjs(a.createdAt)),
      render: (d, pr) => (
        <Tooltip title={dayjs(d).format('YYYY-MM-DD HH:mm')}>
          <Typography.Text type={pr.openDays >= 7 ? 'danger' : undefined}>{dayjs(d).fromNow(true)}</Typography.Text>
        </Tooltip>
      ),
    },
    { title: 'GitHub Actions', dataIndex: 'checks', width: 140, render: (checks) => <GithubChecks checks={checks} /> },
    {
      title: 'Tarjeta', key: 'card', width: 150,
      render: (_, pr) => pr.cardId ? <Tag color="green">Tarjeta #{pr.cardId}</Tag> : (
        <Tooltip title="Crea la tarjeta de este PR en la lista PR, con su autor como responsable y sus comentarios">
          <Button size="small" icon={<PlusOutlined />} loading={creating === pr.rowKey}
            onClick={(e) => { e.stopPropagation(); createCard(pr); }}>Crear tarjeta</Button>
        </Tooltip>
      ),
    },
  ];

  const failing = repos.filter((r) => r.error && r.githubUrl);
  const truncated = repos.filter((r) => r.totalCount > r.pulls.length);

  return (
    <div style={{ padding: 16 }}>
      <Card
        title={<Space>PRs abiertos <Badge count={rows.length} showZero color="#8c8c8c" /></Space>}
        extra={(
          <Space>
            {data && <Typography.Text type="secondary">Actualizado {dayjs(data.fetchedAt).fromNow()}</Typography.Text>}
            <Input.Search allowClear placeholder="Buscar título, autor, rama…" style={{ width: 260 }}
              onChange={(e) => setSearch(e.target.value)} />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(true)}>Actualizar</Button>
          </Space>
        )}
      >
        {error && <Alert type="warning" showIcon message={error} style={{ marginBottom: 16 }} />}
        {failing.length > 0 && (
          <Alert type="warning" showIcon style={{ marginBottom: 16 }}
            message="No se pudieron leer algunos repositorios"
            description={failing.map((r) => <div key={r.key}><b>{r.key}</b>: {r.error}</div>)} />
        )}
        {truncated.length > 0 && (
          <Alert type="info" showIcon style={{ marginBottom: 16 }}
            message={`Se muestran los ${truncated[0].pulls.length} PRs más recientes de: ${truncated.map((r) => r.key).join(', ')}`} />
        )}
        <Table
          rowKey="rowKey"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={false}
          onChange={(_, filters) => setAuthorFilter(filters.author || null)}
          locale={{ emptyText: error ? ' ' : (authorFilter?.length ? 'No hay PRs abiertos con este filtro' : 'No hay PRs abiertos') }}
          onRow={(pr) => ({ onClick: () => window.open(pr.url, '_blank', 'noopener'), style: { cursor: 'pointer' } })}
        />
      </Card>
    </div>
  );
}
