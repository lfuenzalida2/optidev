import React, { useCallback, useEffect, useState } from 'react';
import { App, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip } from 'antd';
import { DeleteOutlined, EditOutlined, PlusOutlined, SafetyOutlined } from '@ant-design/icons';

import { usersApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { useAuth } from '../contexts/AuthContext';

const ROLE_OPTIONS = [{ value: 'member', label: 'Miembro' }, { value: 'admin', label: 'Admin' }];

export default function Users() {
  const { message } = App.useApp();
  const { user: me } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await usersApi.list());
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const open = (user) => {
    setEditing(user);
    form.setFieldsValue(user.id ? { ...user, password: '' } : { role: 'member' });
  };

  const save = async () => {
    const values = await form.validateFields();
    try {
      if (editing.id) {
        const { username, ...changes } = values;
        if (!changes.password) delete changes.password;
        await usersApi.update(editing.id, changes);
      } else {
        await usersApi.create(values);
      }
      setEditing(null);
      form.resetFields();
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const act = async (fn) => {
    try {
      await fn();
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const columns = [
    { title: 'Usuario', dataIndex: 'username' },
    { title: 'Nombre', dataIndex: 'name' },
    { title: 'Email', dataIndex: 'email' },
    { title: 'Rol', dataIndex: 'role', render: (r) => <Tag color={r === 'admin' ? 'gold' : 'default'}>{r}</Tag> },
    {
      title: 'Activo', dataIndex: 'isActive',
      render: (active, u) => (
        <Switch size="small" checked={active} disabled={u.id === me.id}
          onChange={(isActive) => act(() => usersApi.update(u.id, { isActive }))} />
      ),
    },
    {
      title: 'Listo', dataIndex: 'readiness',
      render: (r) => (r?.ready ? <Tag color="green">Listo</Tag> : (
        <Tooltip title={`Falta: ${(r?.checks || []).filter((c) => !c.ok).map((c) => c.label).join(', ')}`}>
          <Tag color="orange">Incompleto</Tag>
        </Tooltip>
      )),
    },
    {
      title: 'MFA', dataIndex: 'mfaEnabled',
      render: (enabled) => (enabled ? <Tag color="green">Activo</Tag> : <Tag>Sin registrar</Tag>),
    },
    {
      title: '', key: 'actions', width: 120,
      render: (_, u) => (
        <Space>
          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => open(u)} />
          {u.mfaEnabled && (
            <Popconfirm title={`¿Rehacer el MFA de ${u.username}?`}
              description="Tendrá que registrarlo de nuevo con su app la próxima vez que ingrese."
              onConfirm={() => act(async () => {
                await usersApi.resetMfa(u.id);
                message.success('MFA reiniciado');
              })}>
              <Tooltip title="Rehacer MFA">
                <Button size="small" type="text" icon={<SafetyOutlined />} />
              </Tooltip>
            </Popconfirm>
          )}
          {u.id !== me.id && (
            <Popconfirm title={`¿Eliminar a ${u.username}?`} onConfirm={() => act(() => usersApi.remove(u.id))}>
              <Button size="small" type="text" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Card title="Usuarios" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => open({})}>Agregar</Button>}>
        <Table rowKey="id" columns={columns} dataSource={users} loading={loading} pagination={false}
          scroll={{ x: 'max-content' }} />
      </Card>
      <Modal open={editing !== null} title={editing?.id ? `Editar ${editing.username}` : 'Nuevo usuario'} okText="Guardar"
        onOk={save} onCancel={() => { setEditing(null); form.resetFields(); }} destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="username" label="Usuario" rules={[{ required: true }]}>
            <Input disabled={Boolean(editing?.id)} />
          </Form.Item>
          <Form.Item name="name" label="Nombre" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email' }]}><Input /></Form.Item>
          <Form.Item name="role" label="Rol"><Select options={ROLE_OPTIONS} disabled={editing?.id === me.id} /></Form.Item>
          <Form.Item name="password" label={editing?.id ? 'Nueva contraseña (opcional)' : 'Contraseña'}
            rules={[{ required: !editing?.id }, { min: 8, message: 'Mínimo 8 caracteres' }]}>
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
