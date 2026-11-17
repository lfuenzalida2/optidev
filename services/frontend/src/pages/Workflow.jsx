import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, App, Breadcrumb, Button, Card, Collapse, Empty, Input, List, Popconfirm, Space, Spin, Tag, Typography,
} from 'antd';
import {
  FileOutlined, FolderFilled, GithubOutlined, PlusOutlined, ReloadOutlined, RobotOutlined, SendOutlined,
} from '@ant-design/icons';

import Markdown from '../components/Markdown';
import { workflowApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { colors, radius } from '../theme';

// La conversación vive solo en este navegador (el backend no guarda estado).
const CHAT_KEY = 'workflowChat';
// Mensajes que se mandan como contexto en cada pregunta (el backend acepta hasta 100).
const HISTORY_LIMIT = 40;
const EXAMPLES = [
  '¿Qué skills y agentes hay, y para qué sirve cada uno?',
  'Resume la documentación de xelbox-backend',
  'Revisa repo-docs/README.md y sugiere mejoras',
];

// Frontmatter YAML (SKILL.md, repo-docs): se muestra aparte, como GitHub; si
// no, el markdown lo renderiza como un título gigante.
const FRONTMATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/;

const parentOf = (path) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '');
const baseName = (path) => path.slice(path.lastIndexOf('/') + 1);

// Link relativo de un markdown ("../x/README.md") → ruta desde la raíz del repo.
function resolvePath(dir, href) {
  const parts = href.startsWith('/') ? [] : dir.split('/').filter(Boolean);
  href.split('/').forEach((part) => {
    if (part === '..') parts.pop();
    else if (part && part !== '.') parts.push(part);
  });
  return parts.join('/');
}

function loadChat() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_KEY)) || [];
  } catch {
    return [];
  }
}

function FileContent({ path, sha, onOpen }) {
  const [file, setFile] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setFile(null);
    setError(null);
    workflowApi.file(path, sha)
      .then((data) => { if (!cancelled) setFile(data); })
      .catch((err) => { if (!cancelled) setError(extractErrorMessage(err)); });
    return () => { cancelled = true; };
  }, [path, sha]);

  // Los links relativos navegan dentro del explorador; los externos abren otra pestaña.
  const overrides = useMemo(() => ({
    a: ({ node, href = '', ...props }) => {
      if (/^([a-z][a-z0-9+.-]*:|#|\/\/)/i.test(href)) return <a href={href} {...props} target="_blank" rel="noreferrer" />;
      const target = resolvePath(parentOf(path), decodeURIComponent(href.split('#')[0]));
      return <a href={`?path=${encodeURIComponent(target)}`} {...props} onClick={(e) => { e.preventDefault(); onOpen(target); }} />;
    },
  }), [path, onOpen]);

  if (error) return <Alert type="warning" showIcon message={error} />;
  if (!file) return <Spin />;
  if (file.content === null) {
    return (
      <Alert type="info" showIcon
        message={file.binary ? 'Archivo binario: no se puede mostrar acá' : 'Archivo demasiado grande para mostrarlo acá'}
        description={<a href={file.githubUrl} target="_blank" rel="noreferrer">Ver en GitHub</a>} />
    );
  }
  if (/\.mdx?$/i.test(path)) {
    const frontmatter = file.content.match(FRONTMATTER);
    return (
      <>
        {frontmatter && <pre className="workflow-code" style={{ marginBottom: 12 }}>{frontmatter[1]}</pre>}
        <Markdown overrides={overrides}>{frontmatter ? file.content.slice(frontmatter[0].length) : file.content}</Markdown>
      </>
    );
  }
  return <pre className="workflow-code">{file.content}</pre>;
}

function FileBrowser({ tree, path, onOpen }) {
  const entry = tree.entries.find((e) => e.path === path);
  const isFile = entry?.type === 'file';
  const dir = isFile ? parentOf(path) : path;
  const children = useMemo(() => tree.entries
    .filter((e) => parentOf(e.path) === dir)
    .sort((a, b) => (a.type === b.type ? a.path.localeCompare(b.path) : a.type === 'dir' ? -1 : 1)), [tree, dir]);
  const readme = !isFile && children.find((e) => e.type === 'file' && /^readme\.md$/i.test(baseName(e.path)));

  const segments = path ? path.split('/') : [];
  const crumbs = [
    { title: <a onClick={() => onOpen('')}>{tree.repository.name}</a> },
    ...segments.map((name, i) => {
      const target = segments.slice(0, i + 1).join('/');
      return { title: i === segments.length - 1 ? name : <a onClick={() => onOpen(target)}>{name}</a> };
    }),
  ];
  const { githubUrl, branch } = tree.repository;
  const githubLink = path ? `${githubUrl}/${isFile ? 'blob' : 'tree'}/${branch}/${path}` : `${githubUrl}/tree/${branch}`;

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Space style={{ width: '100%', justifyContent: 'space-between' }} wrap>
        <Breadcrumb items={crumbs} />
        <a href={githubLink} target="_blank" rel="noreferrer"><GithubOutlined /> Ver en GitHub</a>
      </Space>
      {path && !entry && <Alert type="warning" showIcon message={`No existe "${path}" en ${branch}`} />}
      {isFile ? (
        <FileContent path={path} sha={tree.sha} onOpen={onOpen} />
      ) : entry || !path ? (
        <>
          <List
            size="small"
            bordered
            dataSource={children}
            locale={{ emptyText: 'Carpeta vacía' }}
            renderItem={(e) => (
              <List.Item onClick={() => onOpen(e.path)} style={{ cursor: 'pointer' }}
                extra={e.type === 'file' && <Typography.Text type="secondary">{(e.size / 1024).toFixed(1)} KB</Typography.Text>}>
                <Space>
                  {e.type === 'dir' ? <FolderFilled style={{ color: colors.primary }} /> : <FileOutlined />}
                  {baseName(e.path)}
                </Space>
              </List.Item>
            )}
          />
          {readme && (
            <Card size="small" title={baseName(readme.path)}>
              <FileContent path={readme.path} sha={tree.sha} onOpen={onOpen} />
            </Card>
          )}
        </>
      ) : null}
    </Space>
  );
}

const DIFF_STYLES = {
  add: { background: `${colors.success}26` },
  del: { background: `${colors.danger}26` },
  hunk: { color: colors.primary },
};

function Diff({ text }) {
  if (!text) return <Typography.Text type="secondary">Sin cambios respecto del archivo actual</Typography.Text>;
  return (
    <pre className="workflow-code">
      {text.split('\n').map((line, i) => {
        let kind = null;
        if (line.startsWith('@@')) kind = 'hunk';
        else if (line.startsWith('+') && !line.startsWith('+++')) kind = 'add';
        else if (line.startsWith('-') && !line.startsWith('---')) kind = 'del';
        return <div key={i} style={DIFF_STYLES[kind]}>{line || ' '}</div>;
      })}
    </pre>
  );
}

function fileTag(file) {
  if (file.content === null) return <Tag color="red">Borrar</Tag>;
  if (file.isNew) return <Tag color="green">Nuevo</Tag>;
  return <Tag color="blue">Modificar</Tag>;
}

function Proposal({ msg, approving, onApprove, onDiscard }) {
  const { proposal, pr, discarded } = msg;
  return (
    <Card size="small" style={{ marginTop: 8 }}
      title={<Space size={6}><Tag color="purple">Propuesta</Tag>{proposal.title}</Space>}>
      {proposal.description && <Markdown>{proposal.description}</Markdown>}
      <Collapse size="small" style={{ marginTop: 8 }} items={proposal.files.map((file) => ({
        key: file.path,
        label: <Space size={6}>{fileTag(file)}<Typography.Text code>{file.path}</Typography.Text></Space>,
        children: <Diff text={file.diff} />,
      }))} />
      <div style={{ marginTop: 8 }}>
        {pr && (
          <Alert type="success" showIcon message={(
            <>PR abierto: <a href={pr.url} target="_blank" rel="noreferrer">#{pr.number}</a> (rama {pr.branch})</>
          )} />
        )}
        {!pr && discarded && <Typography.Text type="secondary">Propuesta descartada</Typography.Text>}
        {!pr && !discarded && (
          <Space>
            <Popconfirm title="¿Abrir un PR con estos cambios?" okText="Abrir PR" onConfirm={onApprove}>
              <Button type="primary" icon={<GithubOutlined />} loading={approving}>Aprobar y abrir PR</Button>
            </Popconfirm>
            <Button onClick={onDiscard} disabled={approving}>Descartar</Button>
          </Space>
        )}
      </div>
    </Card>
  );
}

function Chat() {
  const { message } = App.useApp();
  const [messages, setMessages] = useState(loadChat);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [approving, setApproving] = useState(null);
  const bottomRef = useRef(null);

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_KEY, JSON.stringify(messages));
    } catch {
      // Sin storage (modo privado): la conversación dura lo que la pestaña.
    }
  }, [messages]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ block: 'end' }); }, [messages.length, sending]);

  const send = async (text = input.trim()) => {
    if (!text || sending) return;
    const next = [...messages, { role: 'user', content: text }];
    // Solo texto, y el historial tiene que empezar con un mensaje del usuario.
    const history = next.slice(-HISTORY_LIMIT).map(({ role, content }) => ({ role, content }));
    while (history[0].role !== 'user') history.shift();
    setMessages(next);
    setInput('');
    setSending(true);
    try {
      const data = await workflowApi.chat(history);
      setMessages([...next, { role: 'assistant', content: data.reply, proposal: data.proposal }]);
    } catch (err) {
      message.error(extractErrorMessage(err));
      setMessages(messages);
      setInput(text);
    } finally {
      setSending(false);
    }
  };

  const approve = async (index) => {
    const { proposal } = messages[index];
    setApproving(index);
    try {
      const pr = await workflowApi.createPullRequest({
        title: proposal.title,
        description: proposal.description,
        baseSha: proposal.baseSha,
        files: proposal.files.map(({ path, content }) => ({ path, content })),
      });
      // El aviso queda en el historial para que Claude sepa que ya se abrió.
      setMessages((prev) => [
        ...prev.map((m, i) => (i === index ? { ...m, pr } : m)),
        { role: 'assistant', content: `Abrí el PR [#${pr.number}](${pr.url}) con la propuesta "${proposal.title}".` },
      ]);
      message.success(`PR #${pr.number} abierto`);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setApproving(null);
    }
  };

  const discard = (index) => setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, discarded: true } : m)));

  return (
    <Card
      title={<Space><RobotOutlined style={{ color: colors.claude }} />Chat con Claude</Space>}
      extra={<Button size="small" icon={<PlusOutlined />} disabled={!messages.length || sending}
        onClick={() => setMessages([])}>Nueva conversación</Button>}
      style={{ flex: '2 1 0', minWidth: 0, display: 'flex', flexDirection: 'column' }}
      styles={{ body: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12 } }}
    >
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
        {!messages.length && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={(
            <Space direction="vertical">
              <Typography.Text type="secondary">
                Pregunta sobre la documentación, skills y agentes del repositorio, o pide cambios: Claude arma una
                propuesta y, si la apruebas, abre un PR.
              </Typography.Text>
              {EXAMPLES.map((text) => <Button key={text} size="small" onClick={() => send(text)}>{text}</Button>)}
            </Space>
          )} />
        )}
        {messages.map((msg, i) => (msg.role === 'user' ? (
          <div key={i} style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
            <div style={{
              maxWidth: '85%', whiteSpace: 'pre-wrap', padding: '6px 12px', borderRadius: radius.lg,
              background: colors.surfaceSubtle, border: `1px solid ${colors.border}`,
            }}>
              {msg.content}
            </div>
          </div>
        ) : (
          <div key={i} style={{ marginBottom: 12 }}>
            <Typography.Text strong style={{ color: colors.claude }}><RobotOutlined /> Claude</Typography.Text>
            <Markdown>{msg.content}</Markdown>
            {msg.proposal && (
              <Proposal msg={msg} approving={approving === i} onApprove={() => approve(i)} onDiscard={() => discard(i)} />
            )}
          </div>
        )))}
        {sending && <Space><Spin size="small" /><Typography.Text type="secondary">Claude está pensando…</Typography.Text></Space>}
        <div ref={bottomRef} />
      </div>
      {/* Margen a la derecha: el botón flotante de la terminal (fijo abajo a la derecha) tapaba el de enviar. */}
      <Space.Compact style={{ width: 'calc(100% - 48px)' }}>
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          // Enter envía; Shift+Enter agrega un salto de línea.
          onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Escribe tu pregunta o pedido (Enter envía, Shift+Enter nueva línea)"
          autoSize={{ minRows: 1, maxRows: 6 }}
          disabled={sending}
        />
        <Button type="primary" icon={<SendOutlined />} loading={sending} disabled={!input.trim()} onClick={() => send()} />
      </Space.Compact>
    </Card>
  );
}

export default function Workflow() {
  const { message } = App.useApp();
  const [params, setParams] = useSearchParams();
  const path = params.get('path') || '';
  const [tree, setTree] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      setTree(await workflowApi.tree(refresh));
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
      if (refresh) message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  const open = useCallback((target) => setParams(target ? { path: target } : {}), [setParams]);

  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', gap: 16, padding: 16 }}>
      <Card
        title={tree ? `${tree.repository.name} · ${tree.repository.branch}` : 'Repositorio de agentes'}
        extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => load(true)}>Actualizar</Button>}
        style={{ flex: '3 1 0', minWidth: 0, display: 'flex', flexDirection: 'column' }}
        styles={{ body: { flex: 1, minHeight: 0, overflow: 'auto' } }}
      >
        {error && <Alert type="warning" showIcon message={error} style={{ marginBottom: 16 }} />}
        {tree?.truncated && (
          <Alert type="info" showIcon style={{ marginBottom: 16 }}
            message="GitHub truncó el listado: puede que falten archivos en el explorador" />
        )}
        {tree ? <FileBrowser tree={tree} path={path} onOpen={open} /> : !error && <Spin />}
      </Card>
      <Chat />
    </div>
  );
}
