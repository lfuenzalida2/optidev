import React, { useState } from 'react';
import { App, Button, Card, Input, Popconfirm, Space, Typography } from 'antd';
import { GithubOutlined } from '@ant-design/icons';

import { extractErrorMessage } from '../api/axios';
import { useAuth } from '../contexts/AuthContext';

// Token personal de GitHub del usuario. Se guarda cifrado y no se vuelve a
// mostrar: la API solo devuelve el login de GitHub al que pertenece.
export default function GithubTokenCard() {
  const { message } = App.useApp();
  const { user, setGithubToken } = useAuth();
  const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async (value) => {
    setSaving(true);
    try {
      await setGithubToken(value);
      message.success(value ? 'Token de GitHub guardado' : 'Token de GitHub quitado');
      setToken('');
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card title={<Space><GithubOutlined />Token de GitHub</Space>}>
      {user.githubLogin ? (
        <Typography.Paragraph>
          Cargado: cuenta <Typography.Text strong>{user.githubLogin}</Typography.Text>.
          Para reemplazarlo, pegá uno nuevo.
        </Typography.Paragraph>
      ) : (
        <Typography.Paragraph type="secondary">
          Creá un token personal en GitHub (Settings → Developer settings → Personal access tokens) con acceso a
          los repos de Optidev y pegalo acá. Se valida con GitHub y se guarda cifrado.
        </Typography.Paragraph>
      )}
      <Space.Compact style={{ width: '100%' }}>
        <Input.Password placeholder="ghp_… o github_pat_…" value={token} autoComplete="off"
          onChange={(e) => setToken(e.target.value)} onPressEnter={() => token.trim() && save(token.trim())} />
        <Button type="primary" loading={saving} disabled={!token.trim()} onClick={() => save(token.trim())}>
          Guardar
        </Button>
      </Space.Compact>
      {user.githubLogin && (
        <Popconfirm title="¿Quitar el token de GitHub?" onConfirm={() => save(null)}>
          <Button type="link" danger size="small" style={{ paddingInline: 0, marginTop: 8 }}>Quitar token</Button>
        </Popconfirm>
      )}
    </Card>
  );
}
