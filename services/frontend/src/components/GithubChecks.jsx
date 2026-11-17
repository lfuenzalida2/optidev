import React from 'react';
import { Space, Tooltip, Typography, theme } from 'antd';
import { CheckCircleFilled, ClockCircleFilled, CloseCircleFilled } from '@ant-design/icons';

const CHECK_ICON = {
  success: <CheckCircleFilled style={{ color: '#52c41a' }} />,
  failure: <CloseCircleFilled style={{ color: '#f5222d' }} />,
  pending: <ClockCircleFilled style={{ color: '#faad14' }} />,
};
const CHECK_TEXT = { success: 'Pasaron', failure: 'Fallaron', pending: 'Corriendo' };

export default function GithubChecks({ checks }) {
  const { token } = theme.useToken();
  if (!checks) return <Typography.Text type="secondary">—</Typography.Text>;
  const list = (
    <div>
      {checks.items.map((c) => (
        <div key={c.name}>{CHECK_ICON[c.state]} {c.url
          ? <a href={c.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>{c.name}</a>
          : c.name}</div>
      ))}
    </div>
  );
  return (
    <Tooltip title={checks.items.length ? list : null} color={token.colorBgElevated}>
      <Space size={4}>{CHECK_ICON[checks.state]} {CHECK_TEXT[checks.state]}</Space>
    </Tooltip>
  );
}
