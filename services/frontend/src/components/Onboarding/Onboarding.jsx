import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, Card, Modal, Space, Steps, Tag, Typography } from 'antd';
import {
  BranchesOutlined, CommentOutlined, KeyOutlined, ProjectOutlined, PullRequestOutlined, RobotOutlined,
  RocketOutlined, TagsOutlined,
} from '@ant-design/icons';

import McpFlow from './McpFlow';
import ColorTag from '../ColorTag';
import { colors, radius } from '../../theme';

const { Title, Paragraph, Text } = Typography;

const MiniCard = ({ title, repos = [], extra }) => (
  <Card size="small" styles={{ body: { padding: '6px 8px' } }} style={{ marginBottom: 6 }}>
    <div>
      {repos.map((r) => <ColorTag key={r} color={colors.primary}>{r}</ColorTag>)}
    </div>
    <Text style={{ fontSize: 13 }}>{title}</Text>
    {extra && <div style={{ fontSize: 11, color: colors.textMuted, marginTop: 2 }}>{extra}</div>}
  </Card>
);

const MiniColumn = ({ name, children }) => (
  <div style={{ flex: '1 1 0', minWidth: 110, background: colors.column, borderRadius: radius.md, padding: 6 }}>
    <Text strong style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>{name}</Text>
    {children}
  </div>
);

const Illustration = ({ children }) => (
  <div style={{ background: colors.surfaceSubtle, border: `1px solid ${colors.border}`, borderRadius: radius.lg, padding: 12, marginTop: 12 }}>
    {children}
  </div>
);

const buildSteps = (goToTokens) => [
  {
    title: 'Inicio',
    icon: <RocketOutlined />,
    content: (
      <>
        <Title level={4}>Optidev: el tablero de desarrollo de Optidev</Title>
        <Paragraph>
          Acá viven las tareas de desarrollo. Cada tarea es una <b>tarjeta</b> que avanza por columnas a
          medida que se trabaja, igual que en Trello.
        </Paragraph>
        <Paragraph>
          La diferencia: <b>Claude Code puede leer este tablero y hacer las tareas por vos</b>. Toma una
          tarjeta, programa el cambio en el repositorio que corresponde, abre el Pull Request y deja la
          tarjeta lista para que la revises.
        </Paragraph>
        <Paragraph type="secondary">En 5 pasos te mostramos cómo funciona. Podés volver a ver esta guía cuando quieras con el botón “Guía” de arriba.</Paragraph>
      </>
    ),
  },
  {
    title: 'Tablero',
    icon: <ProjectOutlined />,
    content: (
      <>
        <Title level={4}>El tablero</Title>
        <Paragraph>
          Cada <b>columna es un estado</b>. Las tarjetas se mueven <b>arrastrándolas</b> de una columna a otra.
        </Paragraph>
        <ul style={{ paddingInlineStart: 20 }}>
          <li><b>Pendiente</b>: lo que falta hacer.</li>
          <li><b>En proceso</b>: alguien (o Claude) lo está haciendo.</li>
          <li><b>PR</b>: el cambio está en un Pull Request esperando revisión.</li>
          <li><b>Testing</b>: falta probarlo. <b>Hecho</b>: terminado.</li>
        </ul>
        <Paragraph>
          Arriba podés <b>filtrar</b> por repositorio o responsable, y buscar por texto o <Text code>#número</Text>.
          Las tarjetas que ya no sirven se <b>archivan</b> (no se borran) y se recuperan desde “Archivadas”.
        </Paragraph>
        <Illustration>
          <div style={{ display: 'flex', gap: 6 }}>
            <MiniColumn name="Pendiente">
              <MiniCard title="Corregir login" repos={['helios']} />
              <MiniCard title="Nuevo reporte" repos={['ia']} />
            </MiniColumn>
            <MiniColumn name="En proceso"><MiniCard title="Sondas SD-WAN" repos={['helios']} /></MiniColumn>
            <MiniColumn name="PR"><MiniCard title="Fix syslog" repos={['helios']} extra={<><PullRequestOutlined /> 1</>} /></MiniColumn>
          </div>
        </Illustration>
      </>
    ),
  },
  {
    title: 'Tarjetas',
    icon: <TagsOutlined />,
    content: (
      <>
        <Title level={4}>Las tarjetas</Title>
        <Paragraph>
          Creá una con “Agregar tarjeta” al pie de una columna y hacé click para abrirla. Lo importante de una tarjeta:
        </Paragraph>
        <ul style={{ paddingInlineStart: 20 }}>
          <li><b>Descripción</b>: qué hay que hacer, con todo el detalle posible. Claude trabaja con lo que diga acá.</li>
          <li><ColorTag color={colors.primary}>Repositorios</ColorTag> <b>en qué repo hay que trabajar</b>. Es lo que Claude usa para saber dónde programar.</li>
          <li><PullRequestOutlined /> <b>Pull requests</b>: los links a los PRs de la tarea.</li>
          <li><CommentOutlined /> <b>Comentarios e historial</b>: quién hizo qué y cuándo. Lo que hace Claude aparece con la marca <Tag icon={<RobotOutlined />} color="purple">Claude</Tag>.</li>
        </ul>
      </>
    ),
  },
  {
    title: 'Repos',
    icon: <BranchesOutlined />,
    content: (
      <>
        <Title level={4}>Los repositorios</Title>
        <Paragraph>
          En <b>Configuración → Repositorios</b> se cargan los repos de la organización. Cada uno tiene:
        </Paragraph>
        <ul style={{ paddingInlineStart: 20 }}>
          <li><b>Key</b>: el nombre corto con el que se marcan las tarjetas (<Text code>helios</Text>, <Text code>mms-backend</Text>…).</li>
          <li><b>Rama base</b>: desde dónde Claude crea su rama y hacia dónde abre el PR (normalmente <Text code>staging</Text>).</li>
          <li><b>Ruta local</b>: dónde está clonado el repo en la máquina donde corre Claude.</li>
        </ul>
        <Alert
          type="info" showIcon
          message="Si un repositorio no tiene ruta local, Claude no lo puede tocar: se detiene en esa tarjeta y te avisa."
        />
      </>
    ),
  },
  {
    title: 'Claude (MCP)',
    icon: <KeyOutlined />,
    content: (
      <>
        <Title level={4}>Conectar Claude Code al tablero (MCP)</Title>
        <Paragraph>
          <b>MCP</b> es la forma en que Claude Code se conecta a otras herramientas. Con el MCP de Optidev,
          Claude puede <b>ver las tarjetas, moverlas, comentar y registrar los PRs</b>, como lo harías vos desde acá.
        </Paragraph>
        <Illustration><McpFlow /></Illustration>
        <Paragraph style={{ marginTop: 12 }}>
          Para entrar, Claude necesita una credencial propia: un <b>token</b> (empieza con <Text code>odv_</Text>).
          Todo lo que haga con tu token queda <b>a tu nombre</b>, marcado como Claude, y lo podés revocar en cualquier momento.
        </Paragraph>
        <Steps
          direction="vertical" size="small" current={-1}
          items={[
            { title: 'Creá tu token', description: 'En “Mi cuenta y tokens” → Crear token. Se muestra una sola vez.' },
            { title: 'Copiá el comando que te muestra la app', description: <>Es un <Text code>claude mcp add …</Text>: pegalo en la terminal de la máquina donde usás Claude Code.</> },
            { title: 'Reiniciá Claude Code', description: 'Listo: Claude ya ve el tablero.' },
          ]}
        />
        <Button type="primary" icon={<KeyOutlined />} onClick={goToTokens}>Crear mi token ahora</Button>
      </>
    ),
  },
  {
    title: 'Pedir tareas',
    icon: <RobotOutlined />,
    content: (
      <>
        <Title level={4}>Pedirle tareas a Claude</Title>
        <Paragraph>Con el MCP conectado, en Claude Code le pedís cosas como:</Paragraph>
        <Illustration>
          <Space direction="vertical">
            <Text code>Revisá el tablero optidev y hacé las tareas de Pendiente</Text>
            <Text code>Hacé la tarjeta #12</Text>
            <Text code>Creá una tarjeta en Pendiente para helios: “agregar filtro por fecha”</Text>
          </Space>
        </Illustration>
        <Paragraph style={{ marginTop: 12 }}>Por cada tarjeta, Claude:</Paragraph>
        <ol style={{ paddingInlineStart: 20 }}>
          <li>La mueve a <b>En proceso</b>.</li>
          <li>Crea una rama en el repositorio, programa y prueba el cambio.</li>
          <li>Abre el <b>Pull Request</b>, lo registra en la tarjeta, comenta qué hizo y la mueve a <b>PR</b>.</li>
        </ol>
        <Alert
          type="success" showIcon
          message="Vos revisás el PR. Mover la tarjeta a “Hecho” es siempre decisión de una persona, nunca de Claude."
        />
      </>
    ),
  },
];

export default function Onboarding({ open, onClose }) {
  const navigate = useNavigate();
  const [current, setCurrent] = useState(0);

  const close = () => {
    setCurrent(0);
    onClose();
  };
  const goToTokens = () => {
    close();
    navigate('/account');
  };

  const steps = buildSteps(goToTokens);
  const last = current === steps.length - 1;

  return (
    <Modal
      open={open}
      onCancel={close}
      width={820}
      title="Guía rápida de Optidev"
      footer={(
        <Space>
          <Button type="text" onClick={close}>Saltar guía</Button>
          {current > 0 && <Button onClick={() => setCurrent(current - 1)}>Anterior</Button>}
          {last
            ? <Button type="primary" onClick={close}>Empezar</Button>
            : <Button type="primary" onClick={() => setCurrent(current + 1)}>Siguiente</Button>}
        </Space>
      )}
    >
      <Steps
        size="small" current={current} onChange={setCurrent} style={{ marginBottom: 20 }}
        items={steps.map(({ title, icon }) => ({ title, icon }))}
        responsive
      />
      <div style={{ minHeight: 380 }}>{steps[current].content}</div>
    </Modal>
  );
}
