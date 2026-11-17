import React from 'react';
import { Typography } from 'antd';
import { ArrowRightOutlined, ProjectOutlined, RobotOutlined, UserOutlined } from '@ant-design/icons';
import { colors } from '../../theme';

// Diagrama del recorrido de un pedido a Claude: vos → Claude Code → Optidev.
// Se usa en la guía de bienvenida y en la página de tokens.

const Node = ({ icon, title, subtitle, color }) => (
  <div style={{
    flex: '1 1 0', minWidth: 120, textAlign: 'center', padding: '12px 8px', borderRadius: 8,
    border: `1px solid ${color}55`, background: `${color}12`,
  }}>
    <div style={{ fontSize: 26, color }}>{icon}</div>
    <Typography.Text strong style={{ display: 'block' }}>{title}</Typography.Text>
    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{subtitle}</Typography.Text>
  </div>
);

const Arrow = ({ label }) => (
  <div style={{ flex: '0 0 auto', textAlign: 'center', color: colors.textMuted, fontSize: 11, padding: '0 4px' }}>
    <ArrowRightOutlined style={{ fontSize: 18, display: 'block', margin: '0 auto 2px' }} />
    {label}
  </div>
);

export default function McpFlow() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap', justifyContent: 'center' }}>
      <Node icon={<UserOutlined />} title="Vos" subtitle={'"Hacé las tareas de Pendiente"'} color={colors.primary} />
      <Arrow label="pedido" />
      <Node icon={<RobotOutlined />} title="Claude Code" subtitle="programa y abre los PRs" color={colors.claude} />
      <Arrow label="MCP + tu token" />
      <Node icon={<ProjectOutlined />} title="Optidev" subtitle="lee y mueve las tarjetas" color={colors.success} />
    </div>
  );
}
