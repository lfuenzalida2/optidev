import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Alert, App, Badge, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Tooltip, Typography,
} from 'antd';
import {
  ArrowRightOutlined, CheckOutlined, CloseOutlined, CloudUploadOutlined, MergeCellsOutlined, SyncOutlined, ReloadOutlined,
  RobotOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';

import ColorTag from '../components/ColorTag';
import GithubChecks from '../components/GithubChecks';
import MarkdownEditor from '../components/MarkdownEditor';
import PreviewLoadModal from '../components/PreviewLoadModal';
import SkillRunModal from '../components/SkillRunModal';
import UserAvatar from '../components/UserAvatar';
import { useAuth } from '../contexts/AuthContext';
import { cardsApi, githubApi, previewsApi, reposApi, vmsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import {
  APPROVED_LABEL, hasApprovedLabel, hasApprovedPull, hasReviewedLabel, isFullyApproved, isFullyMerged,
} from '../utils/approval';

const PR_STATE = {
  open: { color: 'green', text: 'Abierto' },
  merged: { color: 'purple', text: 'Mergeado' },
  closed: { color: 'red', text: 'Cerrado' },
};
const REVIEW = {
  approved: { color: 'green', text: 'Review aprobado' },
  changes_requested: { color: 'red', text: 'Cambios pedidos' },
  review_required: { color: 'gold', text: 'Falta review' },
};

// Lista a la que vuelve la tarjeta al rechazar (por nombre); se puede cambiar en el modal.
// Sigue siendo un PR (con cambios pendientes). Aprobar no mueve la tarjeta: queda hasta
// que "Mergear PR" la pasa a Hecho.
const REJECT_TARGET = 'pr';
const REVIEWED_LABEL = 'Revisado';
// Lista a la que pasa "Mover" cuando todos los PRs de la tarjeta ya están mergeados.
const DONE_TARGET = 'hecho';

// "owner/repo" de una URL de GitHub (repo o PR), en minúsculas, para cruzar PRs con repos.
const repoSlug = (url) => (url || '').match(/github\.com[/:]([\w.-]+)\/([\w.-]+?)(?:\.git|\/|$)/)
  ?.slice(1).join('/').toLowerCase();

// Botón para cargar la rama del PR en la VM de pruebas, y qué hay cargado ahora.
// Los estados son clickeables: abren el log de esa carga.
function LoadButton({ pull, repo, loaded, onLoad, onShow }) {
  if (!repo?.previewScript || pull.state !== 'open') return null;
  const here = loaded?.branch === pull.headRef;
  return (
    <Space size={6} wrap>
      <Button size="small" icon={<CloudUploadOutlined />} onClick={onLoad}>
        {here && loaded.status === 'success' ? 'Recargar en mi VM' : 'Cargar en mi VM'}
      </Button>
      {here && loaded.status === 'running' && (
        <Tag color="processing" style={{ cursor: 'pointer' }} onClick={onShow}>Cargando… ver log</Tag>
      )}
      {here && loaded.status === 'success' && (
        <Tooltip title={`${loaded.vmName} · ${dayjs(loaded.finishedAt).fromNow()} · ver log`}>
          <Tag color="green" style={{ cursor: 'pointer' }} onClick={onShow}>Cargado {loaded.sha?.slice(0, 7)}</Tag>
        </Tooltip>
      )}
      {here && loaded.status === 'failed' && (
        <Tooltip title="Ver el log">
          <Tag color="red" style={{ cursor: 'pointer' }} onClick={onShow}>Falló la carga</Tag>
        </Tooltip>
      )}
      {loaded && !here && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>en tu VM: {loaded.branch}</Typography.Text>
      )}
    </Space>
  );
}

function PullInfo({ pull, children }) {
  const short = pull.url.replace(/^https?:\/\/github\.com\//, '').replace('/pull/', '#');
  if (pull.error || !pull.state) {
    return (
      <div>
        <a href={pull.url} target="_blank" rel="noreferrer">{short}</a>
        {pull.error && <Typography.Text type="danger"> — {pull.error}</Typography.Text>}
      </div>
    );
  }
  const state = PR_STATE[pull.state];
  const review = REVIEW[pull.reviewDecision];
  return (
    <Space direction="vertical" size={2} style={{ marginBottom: 6 }}>
      <Space size={6} wrap>
        <a href={pull.url} target="_blank" rel="noreferrer">{short}</a>
        <Tag color={state.color}>{state.text}</Tag>
        {pull.isDraft && <Tag>Borrador</Tag>}
        {pull.state === 'open' && pull.mergeable === 'conflicting' && <Tag color="red">Conflictos</Tag>}
        {review && <Tag color={review.color}>{review.text}</Tag>}
        {hasApprovedLabel(pull) && <Tag color="green" icon={<CheckOutlined />}>{APPROVED_LABEL}</Tag>}
      </Space>
      <Space size={10} wrap>
        <Typography.Text code style={{ whiteSpace: 'nowrap' }}>{pull.headRef} → {pull.baseRef}</Typography.Text>
        <GithubChecks checks={pull.checks} />
        <Tooltip title={`${pull.changedFiles} archivos`}>
          <Typography.Text type="secondary" style={{ whiteSpace: 'nowrap' }}>
            <span style={{ color: '#52c41a' }}>+{pull.additions}</span>{' '}
            <span style={{ color: '#f5222d' }}>−{pull.deletions}</span>
          </Typography.Text>
        </Tooltip>
      </Space>
      {children}
    </Space>
  );
}

const MERGE_STATUS = {
  rebased: { color: 'blue', text: 'Rebaseado' },
  merged: { color: 'green', text: 'Mergeado' },
  conflict: { color: 'red', text: 'Conflictos' },
  skipped: { color: 'default', text: 'Salteado' },
  failed: { color: 'red', text: 'Error' },
};

// Resultado de "Mergear PR" o "Rebase", uno por PR (también queda como comentario en la tarjeta).
function MergeResultModal({ result, onClose }) {
  return (
    <Modal open={!!result} title={result && `${result.action === 'rebase' ? 'Rebase' : 'Mergear PR'} · #${result.card.id} ${result.card.title}`}
      onCancel={onClose} onOk={onClose} cancelButtonProps={{ style: { display: 'none' } }} width={680}>
      {result?.results.map((r) => {
        const status = MERGE_STATUS[r.status] || { text: r.status };
        return (
          <div key={r.url} style={{ marginBottom: 12 }}>
            <Space size={6} wrap>
              <Tag color={status.color}>{status.text}</Tag>
              <a href={r.url} target="_blank" rel="noreferrer">
                {r.url.replace(/^https?:\/\/github\.com\//, '').replace('/pull/', '#')}
              </a>
              {r.branch && <Typography.Text code>{r.branch} → {r.base}</Typography.Text>}
            </Space>
            <div><Typography.Text type={['merged', 'rebased'].includes(r.status) ? 'secondary' : 'danger'}>{r.message}</Typography.Text></div>
          </div>
        );
      })}
      <Typography.Text type="secondary">
        {result?.action === 'rebase' ? 'La tarjeta no se movió ni se mergeó nada.' : result?.movedToDone
          ? 'Todos los PRs quedaron mergeados: la tarjeta pasó a Hecho.'
          : 'La tarjeta no se movió: pasala a Hecho cuando todos sus PRs estén mergeados.'}
      </Typography.Text>
    </Modal>
  );
}

// Modal del veredicto: notas (markdown, con capturas) y lista a la que pasa la tarjeta.
function ReviewModal({ review, lists, onClose, onDone }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const approve = review?.verdict === 'approve';

  useEffect(() => {
    if (!review) return;
    const target = lists.find((l) => l.name.trim().toLowerCase() === REJECT_TARGET);
    form.setFieldsValue({ notes: '', listId: target?.id });
  }, [review, lists, form]);

  const submit = async ({ notes, listId }) => {
    setSaving(true);
    try {
      await cardsApi.qa(review.card.id, { approved: approve, listId: approve ? undefined : listId, notes });
      message.success(approve ? 'QA aprobado' : 'QA rechazado');
      onDone();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={!!review}
      title={review && `${approve ? 'Aprobar' : 'Rechazar'} #${review.card.id} ${review.card.title}`}
      okText={approve ? 'Aprobar' : 'Rechazar'}
      okButtonProps={{ danger: !approve, loading: saving }}
      onOk={() => form.submit()}
      onCancel={onClose}
      destroyOnHidden
      width={640}
    >
      {approve ? (
        <Alert type="info" showIcon style={{ marginBottom: 16 }}
          message={`Se pondrá el label "${APPROVED_LABEL}" en los PRs abiertos de la tarjeta`}
          description="La tarjeta no se mueve: queda lista para &quot;Mergear PR&quot; (rebase sobre staging y merge)." />
      ) : (
        <Alert type="warning" showIcon style={{ marginBottom: 16 }}
          message={`Los PRs vuelven a "${REVIEWED_LABEL}" (se les saca "${APPROVED_LABEL}")`}
          description="Siguen abiertos y necesitan cambios: no se mergean hasta que se vuelvan a aprobar." />
      )}
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item
          name="notes"
          label={approve ? 'Notas de la prueba (opcional)' : 'Qué falló'}
          rules={approve ? [] : [{ required: true, whitespace: true, message: 'Contá qué falló para quien la retome' }]}
        >
          <MarkdownEditor autoFocus autoSize={{ minRows: 4, maxRows: 16 }}
            placeholder="Pasos, resultado esperado vs. obtenido… Podés pegar capturas." />
        </Form.Item>
        {!approve && (
          <Form.Item name="listId" label="Mover la tarjeta a" rules={[{ required: true, message: 'Elegí una lista' }]}>
            <Select options={lists.map((l) => ({ value: l.id, label: l.name }))} />
          </Form.Item>
        )}
      </Form>
    </Modal>
  );
}

export default function TestingQueue() {
  const { message } = App.useApp();
  const { user: me } = useAuth();
  const [data, setData] = useState(null);
  const [listIds, setListIds] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [review, setReview] = useState(null);
  const [repos, setRepos] = useState([]);
  const [vms, setVms] = useState([]);
  const [loads, setLoads] = useState([]);
  const [preview, setPreview] = useState(null);
  const [skillRun, setSkillRun] = useState(null);
  const [merging, setMerging] = useState(null);
  const [rebasing, setRebasing] = useState(null);
  const [mergeResult, setMergeResult] = useState(null);
  const [moving, setMoving] = useState(null);

  // Repos (receta de preview), VMs del usuario y qué tiene cargado cada una.
  const loadPreviewInfo = useCallback(async () => {
    try {
      const [r, v, l] = await Promise.all([reposApi.list(), vmsApi.list(), previewsApi.current()]);
      setRepos(r);
      setVms(v);
      setLoads(l);
    } catch {
      // Sin esto la vista sirve igual, solo sin el botón de cargar.
    }
  }, []);

  useEffect(() => { loadPreviewInfo(); }, [loadPreviewInfo]);

  const repoBySlug = useMemo(() => Object.fromEntries(repos.map((r) => [repoSlug(r.githubUrl), r])), [repos]);
  // Lo último cargado por repo (en cualquiera de las VMs del usuario).
  const loadedByRepo = useMemo(() => {
    const out = {};
    loads.forEach((l) => { if (!out[l.repository]) out[l.repository] = l; });
    return out;
  }, [loads]);

  const load = useCallback(async (refresh = false) => {
    setLoading(true);
    try {
      const result = await githubApi.testing(listIds, refresh);
      setData(result);
      setError(null);
    } catch (err) {
      setError(extractErrorMessage(err));
      if (refresh) message.error(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [listIds, message]);

  useEffect(() => { load(); }, [load]);

  const merge = async (card) => {
    setMerging(card.id);
    try {
      const result = await cardsApi.merge(card.id);
      setMergeResult({ card, results: result.results });
      load(true);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setMerging(null);
    }
  };

  const rebase = async (card) => {
    setRebasing(card.id);
    try {
      const result = await cardsApi.rebase(card.id);
      setMergeResult({ card, results: result.results, action: 'rebase' });
      load(true);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setRebasing(null);
    }
  };

  const lists = data?.lists || [];
  const doneList = lists.find((l) => l.name.trim().toLowerCase() === DONE_TARGET);

  const moveToDone = async (card) => {
    setMoving(card.id);
    try {
      await cardsApi.move(card.id, doneList.id);
      message.success(`#${card.id} pasó a ${doneList.name}`);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setMoving(null);
    }
  };

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (data?.cards || []).filter((c) => !q || [
      c.title, `#${c.id}`, c.assignee?.name, ...c.repositories.map((r) => r.key),
      ...c.pulls.flatMap((p) => [p.url, p.headRef, p.title]),
    ].some((v) => v && v.toLowerCase().includes(q)));
  }, [data, search]);

  // Responsables presentes en la cola, para el filtro de la columna.
  const assignees = useMemo(() => {
    const byId = new Map();
    (data?.cards || []).forEach((c) => { if (c.assignee) byId.set(c.assignee.id, c.assignee.name); });
    return [...byId].map(([value, text]) => ({ text, value }));
  }, [data]);

  const columns = [
    {
      title: 'Tarjeta', key: 'card',
      // Marco verde a la izquierda: algún PR de la tarjeta ya está aprobado.
      onCell: (c) => (hasApprovedPull(c) ? { style: { borderInlineStart: '4px solid #52c41a' } } : {}),
      render: (_, c) => (
        <Space direction="vertical" size={4}>
          <Space size={6} wrap>
            <Link to={`/cards/${c.id}`}>
              <Typography.Text type="secondary">#{c.id}</Typography.Text> {c.title}
            </Link>
            {hasApprovedPull(c) && <Tag color="green" icon={<CheckOutlined />}>PR aprobado</Tag>}
          </Space>
          <Space size={4} wrap>
            {c.repositories.map((r) => <ColorTag key={r.key} color={r.color}>{r.key}</ColorTag>)}
          </Space>
        </Space>
      ),
    },
    {
      title: 'Lista', dataIndex: 'listName', width: 110,
      filters: (data?.listIds || []).map((id) => {
        const name = lists.find((l) => l.id === id)?.name;
        return { text: name, value: name };
      }),
      onFilter: (value, c) => c.listName === value,
      render: (name) => <Tag>{name}</Tag>,
    },
    {
      title: 'Responsable', dataIndex: 'assignee', width: 170,
      // Al entrar, la vista viene filtrada por el usuario actual.
      filters: assignees,
      defaultFilteredValue: me ? [me.id] : undefined,
      onFilter: (value, c) => c.assignee?.id === value,
      render: (user) => (user ? <Space size={6}><UserAvatar user={user} size={20} />{user.name}</Space>
        : <Typography.Text type="secondary">—</Typography.Text>),
    },
    {
      title: 'Pull requests', key: 'pulls',
      render: (_, c) => (c.pulls.length ? c.pulls.map((p) => {
        const repo = repoBySlug[repoSlug(p.url)];
        return (
          <PullInfo key={p.url} pull={p}>
            <LoadButton pull={p} repo={repo} loaded={repo && loadedByRepo[repo.key]}
              onLoad={() => setPreview({ repo, branch: p.headRef, card: c, pull: p })}
              onShow={() => setPreview({ repo, branch: p.headRef, card: c, pull: p,
                loadId: loadedByRepo[repo.key].id })} />
            {p.state === 'open' && hasReviewedLabel(p) && (
              <Tooltip title="Corre claude -p con la skill revisar-pr en tu VM">
                <Button size="small" icon={<RobotOutlined />}
                  onClick={() => setSkillRun({ skill: 'revisar-pr', card: c, pull: p })}>Correr revisar-pr</Button>
              </Tooltip>
            )}
            {p.state === 'open' && p.headRef === 'staging' && p.baseRef === 'master' && (
              <Tooltip title="Sube versiones, regenera requirements.json, resuelve sus issues y sube el commit «Version release» a staging (claude -p en tu VM)">
                <Button size="small" icon={<RobotOutlined />}
                  onClick={() => setSkillRun({ skill: 'release', card: c, pull: p })}>Preparar release</Button>
              </Tooltip>
            )}
          </PullInfo>
        );
      })
        : <Typography.Text type="warning">Sin PR registrado</Typography.Text>),
    },
    {
      title: 'Actualizada', dataIndex: 'updatedAt', width: 120,
      sorter: (a, b) => dayjs(a.updatedAt).diff(dayjs(b.updatedAt)),
      render: (d) => <Tooltip title={dayjs(d).format('YYYY-MM-DD HH:mm')}>{dayjs(d).fromNow(true)}</Tooltip>,
    },
    {
      title: 'QA', key: 'actions', width: 210,
      render: (_, c) => {
        // PRs ya mergeados (p. ej. directo en GitHub): no queda nada por aprobar ni mergear.
        if (isFullyMerged(c)) {
          return doneList && c.listId !== doneList.id ? (
            <Tooltip title={`Pasar la tarjeta a ${doneList.name}`}>
              <Button size="small" type="primary" icon={<ArrowRightOutlined />} loading={moving === c.id}
                onClick={() => moveToDone(c)}>Mover</Button>
            </Tooltip>
          ) : <Tag color="purple">Mergeado</Tag>;
        }
        // "Mergear PR" solo si algún PR abierto ya tiene el label "Aprobado"; "Aprobar"
        // desaparece cuando ya no queda ningún PR abierto por aprobar.
        const mergeable = hasApprovedPull(c);
        return (
          <Space wrap>
            {!isFullyApproved(c) && (
              <Button size="small" type="primary" icon={<CheckOutlined />}
                onClick={() => setReview({ card: c, verdict: 'approve' })}>Aprobar</Button>
            )}
            <Button size="small" danger icon={<CloseOutlined />}
              onClick={() => setReview({ card: c, verdict: 'reject' })}>Rechazar</Button>
            <Popconfirm
              title="¿Rebasear sobre la rama base?"
              description="Rebasea cada PR abierto sobre la rama base de su repo (reescribe la rama del PR, push con lease). No mergea. Si hay conflictos se detiene."
              okText="Rebase" cancelText="Cancelar" onConfirm={() => rebase(c)}
            >
              <Button size="small" icon={<SyncOutlined />} loading={rebasing === c.id}
                disabled={(rebasing !== null && rebasing !== c.id) || merging !== null}>Rebase</Button>
            </Popconfirm>
            {mergeable && (
              <Popconfirm
                title="¿Mergear los PRs aprobados?"
                description="Rebasea cada rama sobre staging (reescribe la rama del PR) y la mergea. Si hay conflictos se detiene."
                okText="Mergear" cancelText="Cancelar" onConfirm={() => merge(c)}
              >
                <Button size="small" icon={<MergeCellsOutlined />} loading={merging === c.id}
                  disabled={(merging !== null && merging !== c.id) || rebasing !== null}>Mergear PR</Button>
              </Popconfirm>
            )}
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ padding: 16 }}>
      <Card
        title={<Space>Testing de PRs <Badge count={rows.length} showZero color="#8c8c8c" /></Space>}
        extra={(
          <Space wrap>
            {data && <Typography.Text type="secondary">Actualizado {dayjs(data.fetchedAt).fromNow()}</Typography.Text>}
            <Select
              mode="multiple" style={{ minWidth: 220 }} placeholder="Listas" maxTagCount="responsive"
              value={listIds ?? data?.listIds} onChange={setListIds}
              options={lists.map((l) => ({ value: l.id, label: l.name }))}
            />
            <Input.Search allowClear placeholder="Buscar tarjeta, repo, rama…" style={{ width: 240 }}
              onChange={(e) => setSearch(e.target.value)} />
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => load(true)}>Actualizar</Button>
          </Space>
        )}
      >
        {error && <Alert type="warning" showIcon message={error} style={{ marginBottom: 16 }} />}
        {data?.githubError && (
          <Alert type="warning" showIcon style={{ marginBottom: 16 }}
            message="No se pudo consultar el estado de los PRs" description={data.githubError} />
        )}
        <Table
          rowKey="id"
          size="middle"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={false}
          locale={{ emptyText: error ? ' ' : 'No hay tarjetas esperando QA' }}
        />
      </Card>
      <PreviewLoadModal target={preview} vms={vms} onClose={() => { setPreview(null); loadPreviewInfo(); }}
        onFinished={loadPreviewInfo} />
      <SkillRunModal target={skillRun} vms={vms} onClose={() => setSkillRun(null)} onFinished={() => load(true)} />
      <MergeResultModal result={mergeResult} onClose={() => setMergeResult(null)} />
      <ReviewModal review={review} lists={lists} onClose={() => setReview(null)}
        onDone={() => { setReview(null); load(); }} />
    </div>
  );
}
