import React, { useCallback, useEffect, useState } from 'react';
import dayjs from 'dayjs';
import {
  App, Button, Card, Collapse, Descriptions, Form, Input, List, Modal, Popconfirm, Space, Switch, Tag,
  Tooltip, Typography,
} from 'antd';
import {
  ArrowDownOutlined, ArrowUpOutlined, BellOutlined, BranchesOutlined, CloudServerOutlined, CloudUploadOutlined, DeleteOutlined,
  EditOutlined, GithubOutlined, InfoCircleOutlined, OrderedListOutlined, PlusOutlined, ReloadOutlined, ToolOutlined,
} from '@ant-design/icons';

import ColorTag from '../components/ColorTag';
import ColorPicker from '../components/ColorPicker';
import PreviewEnvCard from '../components/PreviewEnvCard';
import {
  FixModal, HealthDetails, HealthIcon, HealthInfo, HealthProgress, StandardPanel, summarize,
} from '../components/RepoHealth';
import RepoPreviewModal from '../components/RepoPreviewModal';
import SectionsLayout from '../components/SectionsLayout';
import { boardApi, listsApi, notificationsApi, repoHealthApi, reposApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { colors } from '../theme';

// Medios por los que optidev avisa a los admins. Hoy solo la campana del header; un medio
// desactivado no deja de registrar el hecho (p. ej. la solicitud de VM), solo no avisa por ahí.
const CHANNEL_LABELS = { inApp: 'Dentro de optidev (campana del header)' };

function NotificationsCard() {
  const { message } = App.useApp();
  const [channels, setChannels] = useState(null);

  useEffect(() => {
    notificationsApi.channels().then(setChannels).catch((err) => message.error(extractErrorMessage(err)));
  }, [message]);

  const toggle = async (channel, enabled) => {
    try {
      setChannels(await notificationsApi.setChannels({ [channel]: enabled }));
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  return (
    <Card title="Notificaciones" loading={!channels}>
      <Typography.Paragraph type="secondary">
        Medios por los que optidev avisa a los admins (por ejemplo, cuando alguien solicita una VM).
        Desactivar uno no borra lo ya avisado ni impide el pedido: solo deja de avisar por ese medio.
      </Typography.Paragraph>
      <List
        dataSource={Object.entries(channels || {})}
        renderItem={([channel, enabled]) => (
          <List.Item actions={[<Switch key="s" checked={enabled} onChange={(value) => toggle(channel, value)} />]}>
            {CHANNEL_LABELS[channel] || channel}
          </List.Item>
        )}
      />
    </Card>
  );
}

function ListsCard() {
  const { message } = App.useApp();
  const [lists, setLists] = useState([]);

  const load = useCallback(async () => {
    try {
      setLists((await boardApi.get()).lists);
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const move = async (index, delta) => {
    const ids = lists.map((l) => l.id);
    const [id] = ids.splice(index, 1);
    ids.splice(index + delta, 0, id);
    try {
      await listsApi.reorder(ids);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  return (
    <Card title="Orden de las listas">
      <Typography.Paragraph type="secondary">
        Crear, renombrar, eliminar y cambiar el color de las listas se hace desde el menú ⋮ de cada
        lista en el tablero. Claude referencia las listas por nombre:
        si renombrás &quot;En proceso&quot; o &quot;PR&quot;, actualizá también las instrucciones que le des.
      </Typography.Paragraph>
      <List
        dataSource={lists}
        renderItem={(list, index) => (
          <List.Item actions={[
            <Button key="up" size="small" type="text" icon={<ArrowUpOutlined />} disabled={index === 0}
              onClick={() => move(index, -1)} />,
            <Button key="down" size="small" type="text" icon={<ArrowDownOutlined />} disabled={index === lists.length - 1}
              onClick={() => move(index, 1)} />,
          ]}>
            <Space>
              <span style={{
                display: 'inline-block', width: 10, height: 10, borderRadius: '50%',
                background: list.color || colors.column,
              }} />
              {list.name}<Typography.Text type="secondary">({list.cards.length})</Typography.Text></Space>
          </List.Item>
        )}
      />
    </Card>
  );
}

function ReposCard() {
  const { message } = App.useApp();
  const [repos, setRepos] = useState([]);
  const [editing, setEditing] = useState(null); // null = cerrado, {} = nuevo, repo = editar
  const [previewRepo, setPreviewRepo] = useState(null);
  const [health, setHealth] = useState(null);
  const [checking, setChecking] = useState(true);
  const [fixing, setFixing] = useState(null); // { repo: salud del repo, checks }
  const [fixToken, setFixToken] = useState(''); // token temporal: solo en memoria, hasta recargar
  const [checkFilter, setCheckFilter] = useState(null); // chequeo por el que se filtra la lista (null = todos)
  const [search, setSearch] = useState('');
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    try {
      setRepos(await reposApi.list());
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  // Salud de los repos (check verde / cruz roja). Si GitHub no responde, la lista se ve igual, sin íconos.
  const checkHealth = useCallback(async (refresh = false) => {
    setChecking(true);
    try {
      setHealth(await (refresh ? repoHealthApi.refresh() : repoHealthApi.get()));
    } catch (err) {
      setHealth(null);
      if (refresh) message.error(extractErrorMessage(err));
    } finally {
      setChecking(false);
    }
  }, [message]);

  useEffect(() => { checkHealth(); }, [checkHealth]);

  // Tras arreglar, la salud del repo ya viene revisada de nuevo.
  const applyFixed = (item) => setHealth((h) => {
    if (!h) return h;
    const repos = h.repositories.map((r) => (r.key === item.key ? item : r));
    return { ...h, repositories: repos, ...summarize(repos) };
  });

  const healthByKey = Object.fromEntries((health?.repositories || []).map((h) => [h.key, h]));
  // Con un chequeo elegido en "Avance del estándar", solo los repos que todavía no lo cumplen.
  const shownRepos = checkFilter && health
    ? repos.filter((r) => ['fail', 'unknown'].includes(healthByKey[r.key]?.checks[checkFilter]?.status))
    : repos;
  const query = search.trim().toLowerCase();
  const visibleRepos = query
    ? shownRepos.filter((r) => [r.key, r.name, r.description].some((v) => (v || '').toLowerCase().includes(query)))
    : shownRepos;

  const open =(repo) => {
    setEditing(repo);
    form.setFieldsValue(repo.id ? repo : { defaultBranch: 'staging', color: '#1890ff' });
  };

  const save = async () => {
    const values = await form.validateFields();
    try {
      if (editing.id) await reposApi.update(editing.id, values);
      else await reposApi.create(values);
      message.success('Repositorio guardado');
      setEditing(null);
      form.resetFields();
      load();
      checkHealth(); // un repo nuevo (o con otra URL) ya viene revisado por el backend
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const createCard = async (key) => {
    try {
      const out = await repoHealthApi.createCard(key);
      message.success(out.message);
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const remove = async (repo) => {
    try {
      await reposApi.remove(repo.id);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const stop = (e) => e.stopPropagation();

  const items = visibleRepos.map((r) => ({
    key: r.id,
    label: (
      <Space>
        {healthByKey[r.key] && <HealthIcon status={healthByKey[r.key].status} />}
        <ColorTag color={r.color}>{r.key}</ColorTag>{r.name}
        {r.previewScript && <Tag color="green">Se puede cargar en VM</Tag>}
      </Space>
    ),
    extra: (
      <Space onClick={stop} onKeyDown={stop}>
        <Tooltip title="Receta para cargarlo en las VMs de pruebas">
          <Button size="small" type="text" icon={<CloudUploadOutlined />} onClick={() => setPreviewRepo(r)} />
        </Tooltip>
        <Button size="small" type="text" icon={<EditOutlined />} onClick={() => open(r)} />
        <Popconfirm title={`¿Eliminar ${r.key}?`} description="Las tarjetas que lo usan dejan de mostrarlo."
          onConfirm={() => remove(r)}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      </Space>
    ),
    children: (
      <>
      {health && (
        <div style={{ marginBottom: 12 }}>
          <Typography.Text strong>Estándar del repositorio</Typography.Text>
          <div style={{ marginTop: 4 }}>
            <HealthDetails health={healthByKey[r.key]}
              onFix={(check) => setFixing({ repo: healthByKey[r.key], checks: [check] })}
              onCard={() => createCard(r.key)} />
          </div>
          {healthByKey[r.key] && healthByKey[r.key].status !== 'ok' && (
            <Button size="small" icon={<ToolOutlined />} style={{ marginTop: 8 }}
              onClick={() => setFixing({ repo: healthByKey[r.key], checks: null })}>Arreglar todo</Button>
          )}
        </div>
      )}
      <Descriptions size="small" column={1}>
        <Descriptions.Item label="GitHub">
          {r.githubUrl
            ? <a href={r.githubUrl} target="_blank" rel="noreferrer"><GithubOutlined /> {r.githubUrl.replace(/^https?:\/\/(www\.)?github\.com\//, '')}</a>
            : '—'}
        </Descriptions.Item>
        <Descriptions.Item label="Rama base"><Typography.Text code>{r.defaultBranch}</Typography.Text></Descriptions.Item>
        <Descriptions.Item label="Ruta local">
          {r.localPath
            ? <Typography.Text code>{r.localPath}</Typography.Text>
            : <Typography.Text type="secondary">sin acceso</Typography.Text>}
        </Descriptions.Item>
        <Descriptions.Item label="VM de pruebas">
          {r.previewScript
            ? <>con receta{r.previewUrl && <> · <Typography.Text code>{r.previewUrl}</Typography.Text></>}</>
            : <Typography.Text type="secondary">sin receta</Typography.Text>}
        </Descriptions.Item>
        {r.description && <Descriptions.Item label="Descripción">{r.description}</Descriptions.Item>}
      </Descriptions>
      </>
    ),
  }));

  return (
    <Card
      title={<Space>Repositorios<Tooltip title={<HealthInfo health={health} />}><InfoCircleOutlined style={{ cursor: 'help' }} /></Tooltip></Space>}
      extra={(
        <Space wrap>
          {health && <Typography.Text type="secondary">Revisado {dayjs(health.checkedAt).fromNow()}</Typography.Text>}
          <Tooltip title="La revisión queda en caché (se renueva sola una vez al día): esto la vuelve a hacer ahora">
            <Button icon={<ReloadOutlined />} loading={checking} onClick={() => checkHealth(true)}>Revisar estándar</Button>
          </Tooltip>
          <Button icon={<PlusOutlined />} onClick={() => open({})}>Agregar</Button>
        </Space>
      )}>
      <Typography.Paragraph type="secondary">
        La <b>key</b> es el nombre corto con el que se marcan las tarjetas. Claude usa la <b>ruta local</b> y
        la <b>rama base</b> para saber dónde trabajar y desde qué rama crear la suya; sin ruta local, Claude
        no tiene acceso a ese repo.
      </Typography.Paragraph>
      <Collapse style={{ marginBottom: 16 }} items={[{
        key: 'estandar',
        label: <Typography.Text strong>Estándar por defecto de los repositorios</Typography.Text>,
        children: <StandardPanel standard={health?.standard} />,
      }]} />
      <HealthProgress health={health} active={checkFilter} onSelect={setCheckFilter} />
      <Input.Search placeholder="Buscar por key, nombre o descripción" allowClear value={search}
        onChange={(e) => setSearch(e.target.value)} style={{ marginBottom: 16 }} />
      <Collapse items={items} />
      <FixModal target={fixing} token={fixToken} onToken={setFixToken} onClose={() => setFixing(null)}
        onDone={applyFixed} />
      <Modal
        open={editing !== null}
        title={editing?.id ? `Editar ${editing.key}` : 'Nuevo repositorio'}
        onCancel={() => { setEditing(null); form.resetFields(); }}
        onOk={save}
        okText="Guardar"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="key" label="Key" extra="minúsculas, números, punto, guion o guion bajo (ej: helios, mms-backend)"
            rules={[{ required: true }, { pattern: /^[a-z0-9][a-z0-9._-]*$/, message: 'Formato inválido' }]}>
            <Input placeholder="helios" />
          </Form.Item>
          <Form.Item name="name" label="Nombre" rules={[{ required: true }]}>
            <Input placeholder="Optidev Helios" />
          </Form.Item>
          <Form.Item name="githubUrl" label="URL de GitHub">
            <Input placeholder="https://github.com/owner/repo" />
          </Form.Item>
          <Form.Item name="defaultBranch" label="Rama base" rules={[{ required: true }]}>
            <Input placeholder="staging" />
          </Form.Item>
          <Form.Item name="localPath" label="Ruta local (donde corre Claude)">
            <Input placeholder="/root/optidev/optidev-helios" />
          </Form.Item>
          <Form.Item name="description" label="Descripción">
            <Input.TextArea autoSize={{ minRows: 2 }} />
          </Form.Item>
          <Form.Item name="color" label="Color">
            <ColorPicker />
          </Form.Item>
        </Form>
      </Modal>
      <RepoPreviewModal repo={previewRepo} onClose={() => setPreviewRepo(null)}
        onSaved={() => { setPreviewRepo(null); load(); }} />
    </Card>
  );
}

const SECTIONS = [
  { key: 'repositorios', icon: <BranchesOutlined />, label: 'Repositorios', Component: ReposCard },
  { key: 'listas', icon: <OrderedListOutlined />, label: 'Orden de las listas', Component: ListsCard },
  { key: 'preview', icon: <CloudServerOutlined />, label: 'Ambiente de preview', Component: PreviewEnvCard },
  { key: 'notificaciones', icon: <BellOutlined />, label: 'Notificaciones', Component: NotificationsCard },
];

export default function Settings() {
  return <SectionsLayout title="Configuración" sections={SECTIONS} />;
}
