import React from 'react';
import { Tag } from 'antd';
import { RobotOutlined } from '@ant-design/icons';

// Marca lo que se hizo con Claude (MCP) o por API; lo hecho en la web no lleva etiqueta.
export default function ViaTag({ via }) {
  if (via === 'mcp') return <Tag icon={<RobotOutlined />} color="purple" style={{ marginInlineStart: 6 }}>Claude</Tag>;
  if (via === 'api') return <Tag style={{ marginInlineStart: 6 }}>API</Tag>;
  return null;
}
