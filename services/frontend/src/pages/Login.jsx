import React, { useCallback, useEffect, useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Form, Input, QRCode, Spin, Typography } from 'antd';
import { LockOutlined, SafetyOutlined, UserOutlined } from '@ant-design/icons';

import { useAuth } from '../contexts/AuthContext';
import { authApi } from '../api';
import { extractErrorMessage } from '../api/axios';

// Registro del MFA (primer ingreso o después de que un admin lo reinició):
// QR para la app de autenticación y, por si no se puede escanear, la clave.
function MfaSetup({ mfaToken, onError }) {
  const [setup, setSetup] = useState(null);

  useEffect(() => {
    authApi.mfaSetup(mfaToken).then(setSetup).catch(onError);
  }, [mfaToken, onError]);

  if (!setup) return <div style={{ textAlign: 'center', padding: 24 }}><Spin /></div>;
  return (
    <>
      <Typography.Paragraph>
        Para entrar necesitás una app de autenticación (Google Authenticator, Microsoft Authenticator, Authy…).
        Escaneá este código con la app:
      </Typography.Paragraph>
      <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 12 }}>
        <QRCode value={setup.otpauthUri} size={180} bgColor="#fff" color="#000" />
      </div>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        ¿No podés escanearlo? Cargá esta clave a mano:{' '}
        <Typography.Text code copyable style={{ fontSize: 12 }}>{setup.secret}</Typography.Text>
      </Typography.Paragraph>
      <Typography.Paragraph>Después ingresá el código de 6 dígitos que muestra la app.</Typography.Paragraph>
    </>
  );
}

function MfaStep({ mfa, onDone, onBack }) {
  const { verifyMfa } = useAuth();
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // 401 = token de MFA vencido (5 min) o inválido: hay que volver a la contraseña.
  const handleError = useCallback((err) => {
    if (err?.response?.status === 401) {
      onBack(extractErrorMessage(err));
    } else {
      setError(extractErrorMessage(err));
    }
  }, [onBack]);

  const onFinish = async ({ code }) => {
    setLoading(true);
    setError(null);
    try {
      await verifyMfa(mfa.token, code);
      onDone();
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      {mfa.step === 'setup'
        ? <MfaSetup mfaToken={mfa.token} onError={handleError} />
        : (
          <Typography.Paragraph>
            Ingresá el código de 6 dígitos de tu app de autenticación.
          </Typography.Paragraph>
        )}
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item name="code" rules={[{ required: true, pattern: /^\s*\d{6}\s*$/, message: 'Ingresá los 6 dígitos' }]}>
          <Input prefix={<SafetyOutlined />} placeholder="123456" autoFocus autoComplete="one-time-code"
            inputMode="numeric" maxLength={7} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={loading}>
          {mfa.step === 'setup' ? 'Activar y entrar' : 'Verificar'}
        </Button>
        <Button type="link" block onClick={() => onBack(null)} style={{ marginTop: 8 }}>Volver</Button>
      </Form>
    </>
  );
}

export default function Login() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [mfa, setMfa] = useState(null);

  const backToPassword = useCallback((message) => {
    setMfa(null);
    setError(message);
  }, []);

  if (user) return <Navigate to="/" replace />;

  const onFinish = async ({ username, password }) => {
    setLoading(true);
    setError(null);
    try {
      const result = await login(username, password);
      if (result.mfa) setMfa(result.mfa);
      else navigate('/');
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <Card style={{ width: 360, maxWidth: '100%' }}>
        <Typography.Title level={3} style={{ textAlign: 'center' }}>Optidev</Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
          {mfa?.step === 'setup' ? 'Activá la verificación en dos pasos'
            : mfa ? 'Verificación en dos pasos' : 'Tablero de desarrollo Optidev'}
        </Typography.Paragraph>
        {mfa ? (
          <MfaStep mfa={mfa} onDone={() => navigate('/')} onBack={backToPassword} />
        ) : (
          <>
            {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 16 }} />}
            <Form layout="vertical" onFinish={onFinish}>
              <Form.Item name="username" rules={[{ required: true, message: 'Ingresá tu usuario o email' }]}>
                <Input prefix={<UserOutlined />} placeholder="Usuario o email" autoFocus />
              </Form.Item>
              <Form.Item name="password" rules={[{ required: true, message: 'Ingresá tu contraseña' }]}>
                <Input.Password prefix={<LockOutlined />} placeholder="Contraseña" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block loading={loading}>Ingresar</Button>
            </Form>
          </>
        )}
      </Card>
    </div>
  );
}
