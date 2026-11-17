import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, App, Button, Card, Empty, Form, Input, Modal, Popconfirm, Space, Table, Timeline, Typography, Upload,
} from 'antd';
import {
  CheckCircleFilled, CloseCircleFilled, CloudServerOutlined, DeleteOutlined, HistoryOutlined, KeyOutlined, LockOutlined,
  ScheduleOutlined, UploadOutlined, UserOutlined,
} from '@ant-design/icons';
import ImgCrop from 'antd-img-crop';
import McpFlow from '../components/Onboarding/McpFlow';
import dayjs from 'dayjs';
import { Link } from 'react-router-dom';

import ColorPicker from '../components/ColorPicker';
import GithubTokenCard from '../components/GithubTokenCard';
import RoutineCard from '../components/RoutineCard';
import SectionsLayout from '../components/SectionsLayout';
import TestVmsCard from '../components/TestVmsCard';
import UserAvatar from '../components/UserAvatar';
import ViaTag from '../components/ViaTag';
import { authApi, tokensApi, uploadsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { useAuth } from '../contexts/AuthContext';
import { colors } from '../theme';
import { describeActivity } from '../utils/activity';

function ProfileCard() {
  const { message } = App.useApp();
  const { user, updateProfile } = useAuth();
  const [name, setName] = useState(user.name);
  const [color, setColor] = useState(user.avatarColor || colors.primary);
  const [uploading, setUploading] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);

  const save = async (changes, ok = 'Perfil actualizado') => {
    try {
      await updateProfile(changes);
      message.success(ok);
      return true;
    } catch (err) {
      message.error(extractErrorMessage(err));
      return false;
    }
  };

  const uploadPhoto = async ({ file }) => {
    setUploading(true);
    try {
      const { url } = await uploadsApi.image(file);
      await save({ avatarUrl: url }, 'Foto actualizada');
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  return (
    <Card title={<Space><UserOutlined />Mi perfil</Space>}>
      <Space align="start" size={24} wrap>
        <Space direction="vertical" align="center">
          <UserAvatar user={{ ...user, avatarColor: color }} size={88} />
          {/* Antes de subir, recorte circular (encuadre + zoom + rotación) igual a como se ve el avatar. */}
          <ImgCrop cropShape="round" rotationSlider showReset modalTitle="Ajustar foto"
            modalOk="Usar foto" modalCancel="Cancelar" resetText="Restablecer">
            <Upload accept="image/png,image/jpeg,image/gif,image/webp" showUploadList={false} customRequest={uploadPhoto}>
              <Button size="small" icon={<UploadOutlined />} loading={uploading}>
                {user.avatarUrl ? 'Cambiar foto' : 'Subir foto'}
              </Button>
            </Upload>
          </ImgCrop>
          {user.avatarUrl && (
            <Button size="small" type="link" danger onClick={() => save({ avatarUrl: null }, 'Foto quitada')}>
              Quitar foto
            </Button>
          )}
        </Space>
        <div style={{ minWidth: 260 }}>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>Nombre</Typography.Text>
          <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
            <Input value={name} maxLength={100} onChange={(e) => setName(e.target.value)}
              onPressEnter={() => name.trim() && save({ name })} />
            <Button disabled={!name.trim() || name.trim() === user.name} onClick={() => save({ name })}>Guardar</Button>
          </Space.Compact>
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4 }}>
            Color de fondo de tus iniciales {user.avatarUrl && '(se usa si no tenés foto)'}
          </Typography.Text>
          <Space>
            <ColorPicker value={color} onChange={setColor} />
            <Button size="small" disabled={color === (user.avatarColor || colors.primary)}
              onClick={() => save({ avatarColor: color }, 'Color actualizado')}>
              Guardar
            </Button>
            {user.avatarColor && (
              <Button size="small" type="link" onClick={async () => {
                if (await save({ avatarColor: null }, 'Color restablecido')) setColor(colors.primary);
              }}>
                Restablecer
              </Button>
            )}
          </Space>
          <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
            Usuario <Typography.Text code>{user.username}</Typography.Text> · {user.email}
          </Typography.Paragraph>
          <Button icon={<LockOutlined />} onClick={() => setPasswordOpen(true)}>Cambiar contraseña</Button>
        </div>
      </Space>
      <PasswordModal open={passwordOpen} onClose={() => setPasswordOpen(false)} />
    </Card>
  );
}

const mcpCommand = (token) =>
  `claude mcp add --transport http --scope user optidev ${window.location.origin}/mcp \\\n  --header "Authorization: Bearer ${token}"`;

function TokensCard() {
  const { message } = App.useApp();
  const [tokens, setTokens] = useState([]);
  const [name, setName] = useState('');
  const [created, setCreated] = useState(null);

  const load = useCallback(async () => {
    try {
      setTokens(await tokensApi.list());
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const create = async () => {
    try {
      setCreated(await tokensApi.create(name.trim() || 'Claude Code'));
      setName('');
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const columns = [
    { title: 'Nombre', dataIndex: 'name' },
    { title: 'Prefijo', dataIndex: 'prefix', render: (p) => <Typography.Text code>{p}…</Typography.Text> },
    { title: 'Creado', dataIndex: 'createdAt', render: (d) => dayjs(d).format('YYYY-MM-DD') },
    { title: 'Último uso', dataIndex: 'lastUsedAt', render: (d) => (d ? dayjs(d).fromNow() : 'nunca') },
    {
      title: '', key: 'actions', width: 50,
      render: (_, t) => (
        <Popconfirm title="¿Revocar el token?" description="Los clientes que lo usen dejan de funcionar."
          onConfirm={async () => { await tokensApi.revoke(t.id); load(); }}>
          <Button size="small" type="text" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <Card title={<Space><KeyOutlined />Tokens para Claude Code (MCP)</Space>}>
      <Typography.Paragraph>
        El token es la credencial con la que <b>Claude Code se conecta a este tablero</b> (vía MCP) para leer
        las tarjetas, moverlas, comentar y registrar los PRs. Todo lo que haga Claude con tu token queda
        <b> a tu nombre</b>, marcado como “Claude”.
      </Typography.Paragraph>
      <div style={{ marginBottom: 16 }}><McpFlow /></div>
      <ol style={{ paddingInlineStart: 20, color: colors.textSecondary }}>
        <li>Creá un token (uno por cada máquina donde uses Claude Code).</li>
        <li>Copiá el comando que aparece y pegalo en la terminal de esa máquina.</li>
        <li>Reiniciá Claude Code.</li>
      </ol>
      <Typography.Paragraph type="secondary">
        Si una máquina deja de usarse o el token se filtra, revocalo acá: solo esa conexión deja de funcionar.
      </Typography.Paragraph>
      <Space.Compact style={{ width: '100%', marginBottom: 16 }}>
        <Input placeholder="Nombre (ej: Claude Code - mi máquina)" value={name} onChange={(e) => setName(e.target.value)}
          onPressEnter={create} />
        <Button type="primary" onClick={create}>Crear token</Button>
      </Space.Compact>
      <Table rowKey="id" size="small" columns={columns} dataSource={tokens} pagination={false} scroll={{ x: 'max-content' }} />
      <Modal open={Boolean(created)} title="Token creado" onCancel={() => setCreated(null)} width={720}
        footer={<Button type="primary" onClick={() => setCreated(null)}>Listo, ya lo copié</Button>}>
        <Alert type="warning" showIcon style={{ marginBottom: 12 }}
          message="Copialo ahora: no se vuelve a mostrar." />
        <Typography.Paragraph copyable={{ text: created?.token }}>
          <Typography.Text code>{created?.token}</Typography.Text>
        </Typography.Paragraph>
        <Typography.Text strong>Registrarlo en Claude Code:</Typography.Text>
        <Typography.Paragraph copyable={{ text: created ? mcpCommand(created.token) : '' }}>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', background: colors.pageBg, padding: 8 }}>
            {created ? mcpCommand(created.token) : ''}
          </pre>
        </Typography.Paragraph>
      </Modal>
    </Card>
  );
}

function PasswordModal({ open, onClose }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  const close = () => {
    form.resetFields();
    onClose();
  };

  const save = async ({ currentPassword, newPassword }) => {
    setLoading(true);
    try {
      await authApi.changePassword(currentPassword, newPassword);
      message.success('Contraseña actualizada');
      close();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal open={open} title="Cambiar contraseña" onCancel={close} footer={null}>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="currentPassword" label="Contraseña actual" rules={[{ required: true }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item name="newPassword" label="Nueva contraseña" rules={[{ required: true }, { min: 8, message: 'Mínimo 8 caracteres' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item name="confirm" label="Repetir nueva contraseña" dependencies={['newPassword']} rules={[
          { required: true },
          ({ getFieldValue }) => ({
            validator: (_, v) => (!v || v === getFieldValue('newPassword')
              ? Promise.resolve() : Promise.reject(new Error('No coincide'))),
          }),
        ]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Space>
          <Button onClick={close}>Cancelar</Button>
          <Button type="primary" htmlType="submit" loading={loading}>Guardar</Button>
        </Space>
      </Form>
    </Modal>
  );
}

const HISTORY_PAGE = 50; // el backend devuelve de a 50 por defecto

function HistoryCard() {
  const { message } = App.useApp();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);

  const load = useCallback(async (before) => {
    setLoading(true);
    try {
      const page = await authApi.activity(before);
      setItems((prev) => (before ? [...prev, ...page] : page));
      setHasMore(page.length === HISTORY_PAGE);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  return (
    <Card title={<Space><HistoryOutlined />Mi historial</Space>} loading={loading && !items.length}>
      {items.length ? (
        <div style={{ maxHeight: 520, overflowY: 'auto', paddingTop: 8 }}>
          <Timeline
            items={items.map((a) => ({
              color: a.via === 'mcp' ? 'purple' : 'blue',
              children: (
                <span style={{ fontSize: 13 }}>
                  <Link to={`/cards/${a.card.id}`}>#{a.card.id} {a.card.title}</Link>: {describeActivity(a)}
                  <ViaTag via={a.via} />
                  <Typography.Text type="secondary" style={{ fontSize: 12, marginInlineStart: 6 }}>
                    {dayjs(a.createdAt).format('YYYY-MM-DD HH:mm')}
                  </Typography.Text>
                </span>
              ),
            }))}
          />
          {hasMore && (
            <Button block loading={loading} onClick={() => load(items[items.length - 1].id)}>Ver más</Button>
          )}
        </div>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Todavía no hiciste nada en el tablero" />
      )}
    </Card>
  );
}

// Requisitos mínimos para usar optidev (MFA, VM, Claude Code); los calcula el backend.
function ReadinessAlert() {
  const { user } = useAuth();
  const [readiness, setReadiness] = useState(null);

  // Se recalcula cuando cambia el usuario (p. ej. al cargar el token de GitHub).
  useEffect(() => { authApi.readiness().then(setReadiness).catch(() => {}); }, [user]);

  if (!readiness) return null;
  return (
    <Alert showIcon type={readiness.ready ? 'success' : 'warning'}
      message={readiness.ready ? 'Tu usuario está listo para usar optidev' : 'A tu usuario le falta configuración'}
      description={(
        <Space wrap size={[16, 4]}>
          {readiness.checks.map((c) => (
            <span key={c.key}>
              {c.ok
                ? <CheckCircleFilled style={{ color: colors.success }} />
                : <CloseCircleFilled style={{ color: colors.danger }} />} {c.label}
            </span>
          ))}
        </Space>
      )} />
  );
}

const SECTIONS = [
  {
    key: 'perfil', icon: <UserOutlined />, label: 'Perfil',
    Component: () => (
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <ProfileCard />
        <GithubTokenCard />
      </Space>
    ),
  },
  { key: 'vm', icon: <CloudServerOutlined />, label: 'VM de prueba', Component: TestVmsCard },
  { key: 'tokens', icon: <KeyOutlined />, label: 'Tokens', Component: TokensCard },
  { key: 'rutina', icon: <ScheduleOutlined />, label: 'Rutina diaria', Component: RoutineCard },
  { key: 'historial', icon: <HistoryOutlined />, label: 'Mi historial', Component: HistoryCard },
];

export default function Account() {
  return <SectionsLayout title="Mi perfil" sections={SECTIONS} header={<ReadinessAlert />} />;
}
