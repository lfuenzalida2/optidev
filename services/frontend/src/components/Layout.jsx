import React, { useEffect, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { App, Button, Dropdown, Layout as AntLayout, Menu, Tooltip, Typography } from 'antd';
import {
  CloudServerOutlined, DatabaseOutlined, ExperimentOutlined, GithubOutlined, LogoutOutlined, MoonOutlined, ProjectOutlined, QuestionCircleOutlined, RobotOutlined,
  SettingOutlined, SunOutlined, TeamOutlined, ToolOutlined, UserOutlined,
} from '@ant-design/icons';

import { useAuth } from '../contexts/AuthContext';
import { TerminalProvider } from '../contexts/TerminalContext';
import { extractErrorMessage } from '../api/axios';
import NotificationBell from './NotificationBell';
import Onboarding from './Onboarding/Onboarding';
import { colors } from '../theme';
import UserAvatar from './UserAvatar';
import UpdateButton from './UpdateButton';
import ClaudeChat from './Terminal/ClaudeChat';
import TerminalPanel from './Terminal/TerminalPanel';

const { Header, Content } = AntLayout;

export default function Layout() {
  const { user, logout, isAdmin, completeOnboarding, setDarkMode } = useAuth();
  const { message } = App.useApp();
  const [guideOpen, setGuideOpen] = useState(false);

  // La guía se abre sola la primera vez que entra cada usuario.
  useEffect(() => {
    if (user && !user.onboardingCompleted) setGuideOpen(true);
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleDarkMode = () => {
    setDarkMode(!user.darkMode).catch((err) => message.error(extractErrorMessage(err)));
  };

  const closeGuide = () => {
    setGuideOpen(false);
    completeOnboarding();
  };
  const navigate = useNavigate();
  const location = useLocation();
  const selected = location.pathname.startsWith('/cards') ? '/' : location.pathname;

  const items = [
    { key: '/', icon: <ProjectOutlined />, label: 'Tablero' },
    { key: '/github', icon: <GithubOutlined />, label: 'PRs' },
    { key: '/testing', icon: <ExperimentOutlined />, label: 'Testing' },
    { key: '/bases', icon: <DatabaseOutlined />, label: 'Bases' },
    { key: '/workflow', icon: <RobotOutlined />, label: 'Workflow' },
    { key: '/tools', icon: <ToolOutlined />, label: 'Herramientas' },
    ...(isAdmin ? [
      { key: '/settings', icon: <SettingOutlined />, label: 'Configuración' },
      { key: '/users', icon: <TeamOutlined />, label: 'Usuarios' },
      { key: '/vms', icon: <CloudServerOutlined />, label: 'VMs' },
    ] : []),
  ];

  const userMenu = {
    items: [
      { key: 'account', icon: <UserOutlined />, label: 'Mi perfil' },
      { type: 'divider' },
      { key: 'logout', icon: <LogoutOutlined />, label: 'Cerrar sesión', danger: true },
    ],
    onClick: ({ key }) => {
      if (key === 'logout') {
        logout();
        navigate('/login');
      } else {
        navigate('/account');
      }
    },
  };

  return (
    <TerminalProvider>
      <AntLayout style={{ height: '100%' }}>
        <Header style={{ display: 'flex', alignItems: 'center', gap: 24, position: 'sticky', top: 0, zIndex: 10 }}>
          <Typography.Title level={4} style={{ color: colors.onDark, margin: 0, whiteSpace: 'nowrap' }}>
            Optidev
          </Typography.Title>
          <Menu
            theme="dark"
            mode="horizontal"
            selectedKeys={[selected]}
            items={items}
            onClick={({ key }) => navigate(key)}
            style={{ flex: 1, minWidth: 0 }}
          />
          {isAdmin && <UpdateButton />}
          <NotificationBell />
          <Button type="text" icon={<QuestionCircleOutlined />} style={{ color: colors.onDark }} onClick={() => setGuideOpen(true)}>
            Guía
          </Button>
          <Tooltip title={user?.darkMode ? 'Modo claro' : 'Modo oscuro'}>
            <Button type="text" icon={user?.darkMode ? <SunOutlined /> : <MoonOutlined />} style={{ color: colors.onDark }}
              onClick={toggleDarkMode} aria-label={user?.darkMode ? 'Activar modo claro' : 'Activar modo oscuro'} />
          </Tooltip>
          <Dropdown menu={userMenu} placement="bottomRight">
            <span style={{ color: colors.onDark, cursor: 'pointer', whiteSpace: 'nowrap' }}>
              <UserAvatar user={user} size="small" style={{ marginRight: 8 }} />
              {user?.name}
            </span>
          </Dropdown>
        </Header>
        <Content style={{ overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
          <Outlet />
        </Content>
        <Onboarding open={guideOpen} onClose={closeGuide} />
        {/* Dock abajo a la derecha: Claude a la izquierda y las terminales a la derecha, lado a lado y sin taparse.
            El contenedor no captura clics; cada hijo sí. */}
        <div style={{
          position: 'fixed', left: 16, right: 16, bottom: 16, zIndex: 1000, display: 'flex', alignItems: 'flex-end',
          justifyContent: 'flex-end', gap: 12, pointerEvents: 'none',
        }}>
          <ClaudeChat />
          <TerminalPanel />
        </div>
      </AntLayout>
    </TerminalProvider>
  );
}
