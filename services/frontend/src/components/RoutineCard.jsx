import React, { useEffect } from 'react';
import { Card, Space, Tag, Typography } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import dayjs from 'dayjs';

import { useAuth } from '../contexts/AuthContext';
import { colors } from '../theme';

// Estado de la rutina diaria. Se instala en Herramientas (timer de systemd en la
// VM, ver vm_doctor_service); al instalarla se prueba una vez y la tool
// report_routine_run del MCP marca la última corrida (requisito de "usuario listo").
export default function RoutineCard() {
  const { user, refreshUser } = useAuth();
  // La corrida la registra la VM por el MCP: el usuario cargado al entrar puede estar desactualizado.
  useEffect(() => { refreshUser(); }, [refreshUser]);
  const lastRun = user.routineLastRunAt;

  return (
    <Card title={<Space><ClockCircleOutlined />Rutina diaria (lunes a viernes, 1:00)</Space>}
      extra={lastRun ? <Tag color="green">Configurada</Tag> : <Tag color="orange">Sin configurar</Tag>}>
      <Typography.Paragraph style={{ color: colors.textSecondary }}>
        Claude Code revisa tus tarjetas de «Pendiente» y «En proceso» todas las noches en tu VM de pruebas, avanza
        las que puede y abre PRs. Se instala desde <Link to="/tools">Herramientas → Rutina diaria en mi VM</Link>.
      </Typography.Paragraph>
      <Typography.Text type="secondary">
        {lastRun ? `Última corrida: ${dayjs(lastRun).format('YYYY-MM-DD HH:mm')}` : 'Todavía no corrió nunca.'}
      </Typography.Text>
    </Card>
  );
}
