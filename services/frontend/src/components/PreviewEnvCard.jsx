import React, { useCallback, useEffect, useState } from 'react';
import { Alert, App, Button, Card, Descriptions, Divider, Form, Input, InputNumber, Space, Tag, Typography } from 'antd';
import { CloudServerOutlined, DatabaseOutlined, SafetyOutlined } from '@ant-design/icons';

import { previewEnvApi } from '../api';
import { extractErrorMessage } from '../api/axios';

// Configuración (solo admins) del ambiente de preview: Vault + MySQL central.
// De acá salen los .env de los servicios cuando se carga una rama en una VM.
export default function PreviewEnvCard() {
  const { message } = App.useApp();
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(null);
  const [vaultForm] = Form.useForm();
  const [mysqlForm] = Form.useForm();

  const load = useCallback(async () => {
    try {
      const s = await previewEnvApi.status();
      setStatus(s);
      if (s.mysql) mysqlForm.setFieldsValue({ ...s.mysql, adminPassword: '' });
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }, [message, mysqlForm]);

  useEffect(() => { load(); }, [load]);

  const run = async (key, fn, ok) => {
    setBusy(key);
    try {
      const result = await fn();
      message.success(ok(result));
      return result;
    } catch (err) {
      message.error(extractErrorMessage(err));
      return null;
    } finally {
      setBusy(null);
    }
  };

  const connect = async ({ addr, token }) => {
    const result = await run('vault', () => previewEnvApi.connectVault(addr, token),
      (r) => `Vault conectado: ${r.steps.join(', ')}`);
    if (result) {
      vaultForm.setFieldValue('token', '');
      setStatus(result);
    }
  };

  const saveMysql = async (values) => {
    const result = await run('mysql', () => previewEnvApi.setMysql(values), () => 'MySQL central guardado');
    if (result) setStatus(result);
  };

  const prepare = () => run('me', previewEnvApi.prepareMine,
    (r) => `Tu ambiente está listo: mount ${r.mount}/, base ${r.database} (usuario ${r.dbUser})`);

  return (
    <Card title={<Space><CloudServerOutlined />Ambiente de preview</Space>}>
      <Typography.Paragraph type="secondary">
        Al cargar una rama en una VM de pruebas, optidev arma el <Typography.Text code>.env</Typography.Text> de
        cada servicio a partir de su <Typography.Text code>requirements.json</Typography.Text>: valores de{' '}
        <Typography.Text code>preview/&lt;repo&gt;/&lt;servicio&gt;</Typography.Text> en el Vault, y la base{' '}
        <Typography.Text code>db_&lt;usuario&gt;</Typography.Text> y el mount{' '}
        <Typography.Text code>&lt;usuario&gt;/</Typography.Text> de quien carga (se crean solos).
      </Typography.Paragraph>

      <Divider orientation="left" plain><SafetyOutlined /> Vault</Divider>
      {status && (
        <Descriptions size="small" column={1} style={{ marginBottom: 12 }}>
          <Descriptions.Item label="Estado">
            {status.connected ? <Tag color="green">Conectado a {status.addr}</Tag> : <Tag>Sin conectar</Tag>}
          </Descriptions.Item>
        </Descriptions>
      )}
      {status?.error && <Alert type="error" showIcon message={status.error} style={{ marginBottom: 12 }} />}
      <Form form={vaultForm} layout="vertical" onFinish={connect} autoComplete="off"
        initialValues={{ addr: 'http://vault0.optidev.local:8200' }}>
        <Form.Item name="addr" label="Dirección" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="token" label="Token de admin" rules={[{ required: true, message: 'Obligatorio' }]}
          extra="Se usa una sola vez para configurar el Vault (AppRole, mount preview/ y la AppRole de optidev) y no se guarda. Después conviene revocarlo.">
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Button type="primary" htmlType="submit" loading={busy === 'vault'}>
          {status?.connected ? 'Reconectar' : 'Conectar'}
        </Button>
      </Form>

      <Divider orientation="left" plain><DatabaseOutlined /> MySQL central</Divider>
      <Form form={mysqlForm} layout="vertical" onFinish={saveMysql} autoComplete="off" disabled={!status?.connected}
        initialValues={{ port: 3306, adminUser: 'root' }}>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="host" label="Host" style={{ flex: 1 }} rules={[{ required: true }]}>
            <Input placeholder="mysql0.optidev.local" />
          </Form.Item>
          <Form.Item name="port" label="Puerto" style={{ width: 120 }} rules={[{ required: true }]}>
            <InputNumber min={1} max={65535} style={{ width: '100%' }} />
          </Form.Item>
        </div>
        <Form.Item name="adminUser" label="Usuario admin" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="adminPassword" label="Contraseña del admin"
          extra={status?.mysql ? 'Vacía = se conserva la guardada.' : 'Se guarda en el Vault (preview/_platform/mysql).'}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" loading={busy === 'mysql'}>Guardar y probar</Button>
          <Button onClick={prepare} loading={busy === 'me'} disabled={!status?.mysql}>Preparar mi ambiente</Button>
        </Space>
      </Form>
    </Card>
  );
}
