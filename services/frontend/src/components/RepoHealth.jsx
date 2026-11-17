import React, { useEffect, useState } from 'react';
import { Alert, App, Button, Descriptions, Input, Modal, Progress, Space, Tag, Tooltip, Typography } from 'antd';
import {
  CheckCircleFilled, CloseCircleFilled, ExclamationCircleFilled, InfoCircleFilled, MinusCircleFilled,
  PlusOutlined, QuestionCircleFilled, ToolOutlined,
} from '@ant-design/icons';

import ColorTag from './ColorTag';
import { repoHealthApi } from '../api';
import { extractErrorMessage } from '../api/axios';

export const CHECKS = [
  { key: 'protection', title: 'Bloqueo de ramas' },
  { key: 'staging', title: 'Rama staging' },
  { key: 'labels', title: 'Labels' },
  { key: 'private', title: 'Privado' },
  { key: 'actions', title: 'GitHub Actions' },
  { key: 'claude-md', title: 'CLAUDE.md' },
  { key: 'claude-config', title: 'Config de Claude' },
  { key: 'docs', title: 'Documentación' },
  { key: 'ci-quality', title: 'CI (tests y lint)' },
  { key: 'staging-protection', title: 'Staging protegido' },
  { key: 'marketplace-ci', title: 'CI del marketplace' },
];

// Qué hace el arreglo de cada chequeo (se le muestra a quien lo va a aplicar).
const FIXABLE = {
  protection: 'Protege master: PR obligatorio (sin exigir aprobaciones), también para admins, sin force push ni borrado. Si ya tiene protección, solo le agrega el PR obligatorio.',
  staging: 'Crea la rama staging desde master.',
  labels: 'Deja los labels exactamente como el estándar: crea los que faltan, corrige los colores y BORRA los que no son del estándar (se quitan también de las issues y PRs que los tengan).',
  private: 'Pasa el repositorio a privado.',
  'claude-md': 'Abre un PR contra staging que agrega a CLAUDE.md el enlace a repo-docs y la sección «Verificar» (solo si los comandos se deducen de la raíz del repo; si no, queda a mano). Conserva lo que ya tiene.',
  'claude-config': 'Abre un PR contra staging que agrega el marketplace optidev-tools y habilita los plugins en .claude/settings.json, conservando el resto del archivo.',
  'ci-quality': 'Abre un PR contra staging con un .github/workflows/ci.yml que corre los tests y el lint que faltan, deducidos de la raíz del repo (Node con yarn/npm; Python con pytest/ruff). No toca workflows existentes; si no se puede deducir, queda a mano.',
  actions: 'Habilita Actions y los workflows estándar deshabilitados. Los que faltan hay que agregarlos a mano; los de más no se tocan.',
};
const RESULT = {
  fixed: { color: 'green', text: 'Arreglado' },
  ok: { color: 'default', text: 'Ya estaba bien' },
  manual: { color: 'orange', text: 'A mano' },
  failed: { color: 'red', text: 'Falló' },
};

const STATUS = {
  ok: { Icon: CheckCircleFilled, color: '#52c41a', text: 'Cumple el estándar' },
  fail: { Icon: CloseCircleFilled, color: '#f5222d', text: 'Algo no cumple el estándar' },
  unknown: { Icon: QuestionCircleFilled, color: '#8c8c8c', text: 'No se pudo verificar' },
  na: { Icon: MinusCircleFilled, color: '#bfbfbf', text: 'No aplica a este repositorio' },
};
// Un chequeo informativo que no se cumple avisa, pero no cuenta como falla.
const INFO_FAIL = { Icon: ExclamationCircleFilled, color: '#faad14', text: 'No se cumple, pero es informativo: no cuenta como falla' };

// Check verde (bien), cruz roja (mal), aviso naranja (mal, pero informativo), "-" gris (no aplica) o "?" gris
// (no se pudo leer) de un repo o de un chequeo.
export function HealthIcon({ status, severity, title }) {
  const { Icon, color, text } = (status === 'fail' && severity === 'info' ? INFO_FAIL : STATUS[status]) || STATUS.unknown;
  return <Tooltip title={title || text}><Icon style={{ color }} /></Tooltip>;
}

// Los chequeos que "Arreglar todo" aplica: los exigidos que el repo no cumple (mismo criterio que el backend).
const defaultFixes = (repo) => CHECKS.map((c) => c.key).filter((k) => {
  const check = repo.checks[k];
  return !['ok', 'na'].includes(check.status) && check.severity === 'required';
});

// Conteos que se muestran arriba de la lista, recalculados cuando se arregla un repo.
export const summarize = (repos) => ({
  summary: Object.fromEntries(['ok', 'fail', 'unknown'].map((s) => [s, repos.filter((r) => r.status === s).length])),
  checkSummary: Object.fromEntries(CHECKS.map((c) => [c.key, Object.fromEntries(
    ['ok', 'fail', 'unknown', 'na'].map((s) => [s, repos.filter((r) => r.checks[c.key]?.status === s).length]),
  )])),
});

// Qué se revisa (para el tooltip informativo).
export function HealthInfo({ health }) {
  return (
    <div>
      <div>Revisa que todos los repositorios cargados sigan el mismo estándar:</div>
      <ul style={{ margin: '4px 0', paddingInlineStart: 18 }}>
        <li>Sin push directo a {(health?.branches || ['master']).join('/')}.</li>
        <li>Existe la rama staging: de ahí salen siempre los PR.</li>
        <li>
          Exactamente los mismos labels y colores, ni uno más ni uno menos
          {health?.referenceRepo ? ` (los de ${health.referenceRepo})` : ' (Aprobado y Revisado; se puede elegir un repo de referencia)'}.
        </li>
        <li>Repositorio privado.</li>
        <li>Como mínimo los mismos workflows de GitHub Actions en todos (los de más se muestran como información).</li>
        <li>CLAUDE.md con el enlace a su documentación y los comandos de verificación.</li>
        <li>.claude/settings.json con el marketplace y los plugins de Optidev.</li>
        <li>Documentación del repo en el repo de agentes, sin quedar muy atrás del código.</li>
        <li>Un CI que corra tests y lint, y la rama staging también sin push directo.</li>
        <li>Solo en el repo de agentes: master exige el workflow de validación del marketplace.</li>
      </ul>
      <div>La revisión queda en caché (se renueva sola una vez al día). «Sin verificar» es que no se pudo leer (p. ej. falta de permisos del token) y no cuenta como falla.</div>
      <div>Los chequeos «Informativos» se muestran, pero no cuentan como falla; los que dicen «No aplica» no corresponden a ese repo.</div>
    </div>
  );
}

// Lo que está bien y lo que está mal de un repo, chequeo por chequeo.
// `onFix(checkKey)` agrega el botón de arreglar a cada chequeo que no está OK;
// `onCard()` el de crear la tarjeta de Backlog con lo pendiente.
export function HealthDetails({ health, onFix, onCard }) {
  if (!health) return <Typography.Text type="secondary">Todavía sin revisar.</Typography.Text>;
  return (
    <Space direction="vertical" size={4}>
      {health.error && <Typography.Text type="danger">{health.error}</Typography.Text>}
      {CHECKS.map((c) => {
        const check = health.checks[c.key];
        return (
          <Space key={c.key} size={8} align="start">
            <HealthIcon status={check.status} severity={check.severity} />
            <span>
              <Typography.Text strong>{c.title}:</Typography.Text>{' '}
              {check.severity === 'info' && <Tag>Informativo</Tag>}
              <Typography.Text type={check.status === 'fail' && check.severity !== 'info' ? 'danger' : 'secondary'}>{check.message}</Typography.Text>
              {onFix && !['ok', 'na'].includes(check.status) && FIXABLE[c.key] && (
                <Button size="small" type="link" icon={<ToolOutlined />} onClick={() => onFix(c.key)}>Arreglar</Button>
              )}
            </span>
          </Space>
        );
      })}
      {onCard && Object.values(health.checks).some((c) => ['fail', 'unknown'].includes(c.status)) && (
        <Button size="small" icon={<PlusOutlined />} onClick={onCard}>Crear tarjeta con lo pendiente</Button>
      )}
      {health.checks.actions?.extra?.length > 0 && (
        <Space size={8} align="start">
          <Tooltip title="Información: tener más workflows que el mínimo no es un error"><InfoCircleFilled style={{ color: '#1677ff' }} /></Tooltip>
          <span>
            <Typography.Text strong>Workflows adicionales:</Typography.Text>{' '}
            {health.checks.actions.extra.map((w) => <Tag key={w} color="blue">{w}</Tag>)}
          </span>
        </Space>
      )}
    </Space>
  );
}

// Pide el token temporal y aplica los arreglos de un repo.
// `target` = { repo, checks } (checks null = todos los que no estén OK); `token`/`onToken`: se recuerda
// solo en memoria hasta recargar la página.
export function FixModal({ target, token, onToken, onClose, onDone }) {
  const { message } = App.useApp();
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState(null);

  useEffect(() => { setResults(null); }, [target]);

  const run = async () => {
    setRunning(true);
    try {
      const out = await repoHealthApi.fix(target.repo.key, token, target.checks);
      setResults(out.results);
      onDone(out.repository);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setRunning(false);
    }
  };

  const checks = target?.checks || (target ? defaultFixes(target.repo) : []);
  return (
    <Modal open={!!target} title={target && `Arreglar ${target.repo.key}`} onCancel={onClose} destroyOnClose
      footer={[
        <Button key="close" onClick={onClose}>{results ? 'Cerrar' : 'Cancelar'}</Button>,
        !results && <Button key="run" type="primary" danger icon={<ToolOutlined />} loading={running}
          disabled={token.trim().length < 20} onClick={run}>Aplicar</Button>,
      ]}>
      {!results ? (
        <>
          <Alert type="warning" showIcon style={{ marginBottom: 12 }}
            message="Cambia la configuración del repositorio en GitHub"
            description="Se hace con el token que pegues acá: necesita permisos de administración del repo y, para los arreglos que abren un PR, escritura en Contents y Pull requests. Se usa solo para esta operación; el backend no lo guarda." />
          <Typography.Paragraph strong style={{ marginBottom: 4 }}>Qué se va a hacer</Typography.Paragraph>
          <ul style={{ paddingInlineStart: 18 }}>
            {checks.filter((k) => target?.repo.checks[k]?.status !== 'ok').map((k) => (
              <li key={k}><b>{CHECKS.find((c) => c.key === k).title}:</b> {FIXABLE[k]}</li>
            ))}
          </ul>
          <Input.Password autoFocus placeholder="Token de GitHub (ghp_… o github_pat_…)" value={token}
            onChange={(e) => onToken(e.target.value)} onPressEnter={() => token.trim().length >= 20 && run()}
            autoComplete="off" />
        </>
      ) : (
        <Space direction="vertical" size={8}>
          {CHECKS.filter((c) => results[c.key]).map((c) => (
            <div key={c.key}>
              <Tag color={RESULT[results[c.key].status].color}>{RESULT[results[c.key].status].text}</Tag>
              <Typography.Text strong>{c.title}:</Typography.Text>{' '}
              <Typography.Text type={results[c.key].status === 'failed' ? 'danger' : 'secondary'}>
                {results[c.key].message}
              </Typography.Text>
            </div>
          ))}
          {Object.keys(results).length === 0 && <Typography.Text type="secondary">No había nada que arreglar.</Typography.Text>}
        </Space>
      )}
    </Modal>
  );
}

// El estándar único que se exige a todos los repos (lo mismo para cada uno).
export function StandardPanel({ standard }) {
  if (!standard) return <Typography.Text type="secondary">Se muestra después de la primera revisión.</Typography.Text>;
  const codes = (items) => items.map((i) => <Typography.Text code key={i}>{i}</Typography.Text>);
  // Título del chequeo con su severidad y a qué repos aplica (si no es a todos).
  const label = (title, key) => {
    const spec = standard.checks?.find((c) => c.key === key);
    return (
      <>
        {title}
        {spec?.severity === 'info' && <> <Tag>Informativo</Tag></>}
        {spec?.repos && <> <Tag>Solo en {spec.repos.join(', ')}</Tag></>}
      </>
    );
  };
  return (
    <Descriptions size="small" column={1} bordered>
      <Descriptions.Item label={label('Bloqueo de ramas', 'protection')}>
        <div>En {codes(standard.branches)}:</div>
        <ul style={{ margin: '4px 0 0', paddingInlineStart: 18 }}>
          {standard.protection.map((rule) => <li key={rule}>{rule}</li>)}
        </ul>
      </Descriptions.Item>
      <Descriptions.Item label={label('Rama staging', 'staging')}>
        Existe {codes([standard.stagingBranch])}: los PR salen siempre de ahí.
      </Descriptions.Item>
      <Descriptions.Item label={label('Labels', 'labels')}>
        {standard.labels
          ? <Space size={[4, 4]} wrap>{standard.labels.map((l) => <ColorTag key={l.name} color={`#${l.color}`}>{l.name}</ColorTag>)}</Space>
          : <Typography.Text type="danger">{standard.error}</Typography.Text>}
        <div>
          <Typography.Text type="secondary">
            {standard.labelsExact ? 'Estos y ningún otro, con estos colores, en todos los repos.' : 'Los mismos labels con los mismos colores en todos los repos.'}
          </Typography.Text>
        </div>
      </Descriptions.Item>
      <Descriptions.Item label={label('Visibilidad', 'private')}>Repositorio privado</Descriptions.Item>
      <Descriptions.Item label={label('GitHub Actions', 'actions')}>
        Como mínimo estos workflows, activos, en todos los repos (puede haber más; se muestran como información):
        <ul style={{ margin: '4px 0 0', paddingInlineStart: 18 }}>
          {standard.workflows.map((w) => <li key={w}>{w}</li>)}
        </ul>
        <Typography.Text type="secondary">Cambia a dónde apunta cada uno, no cuáles son.</Typography.Text>
      </Descriptions.Item>
      <Descriptions.Item label={label('CLAUDE.md', 'claude-md')}>
        Existe en la raíz del repo, con un enlace a su documentación ({codes(['repo-docs/<repo>'])} en el repo de agentes)
        y una sección «Verificar» con los comandos de prueba y build.
      </Descriptions.Item>
      <Descriptions.Item label={label('Config de Claude', 'claude-config')}>
        {codes(['.claude/settings.json'])} declara el marketplace {codes([standard.claude?.marketplace || ''])} y
        habilita {codes(standard.claude?.plugins || [])}.
      </Descriptions.Item>
      <Descriptions.Item label={label('Documentación', 'docs')}>
        Existe {codes(['repo-docs/<repo>/README.md'])} en el repo {standard.docs?.repo ? codes([standard.docs.repo]) : 'de agentes'} y
        el commit que dice documentar está a lo más a {standard.docs?.maxCommitsBehind ?? '—'} commits de la rama documentada.
      </Descriptions.Item>
      <Descriptions.Item label={label('CI (tests y lint)', 'ci-quality')}>
        Algún workflow de {codes(['.github/workflows'])} corre tests y lint. Se detecta por los comandos más comunes
        (pytest, jest, yarn test, eslint, ruff…): es una heurística, no ejecuta nada.
      </Descriptions.Item>
      <Descriptions.Item label={label('Staging protegido', 'staging-protection')}>
        {codes([standard.stagingBranch])} tampoco acepta push directo (PR obligatorio o push restringido).
      </Descriptions.Item>
      <Descriptions.Item label={label('CI del marketplace', 'marketplace-ci')}>
        {codes(standard.branches.slice(0, 1))} exige status checks y existe el workflow {codes([standard.marketplaceWorkflow || ''])}.
      </Descriptions.Item>
      <Descriptions.Item label="Origen">
        {standard.referenceRepo
          ? <>Los labels son los del repo <Typography.Text code>{standard.referenceRepo}</Typography.Text> (<Typography.Text code>REPO_HEALTH_REFERENCE_REPO</Typography.Text>); los workflows, la lista fija de arriba.</>
          : <>Labels por defecto (sin repo de referencia: se puede definir con <Typography.Text code>REPO_HEALTH_REFERENCE_REPO</Typography.Text>); los workflows, la lista fija de arriba.</>}
      </Descriptions.Item>
    </Descriptions>
  );
}

// Avance del estándar: por chequeo, cuántos repos lo cumplen. `active` es el chequeo por el que está filtrada la
// lista (o null); `onSelect(key)` la filtra a los repos que no lo cumplen.
export function HealthProgress({ health, active, onSelect }) {
  if (!health?.checkSummary) return null;
  const specs = Object.fromEntries((health.standard?.checks || []).map((s) => [s.key, s]));
  return (
    <div style={{ marginBottom: 16 }}>
      <Typography.Text strong>Avance del estándar</Typography.Text>
      {CHECKS.map((c) => {
        const { ok, fail, unknown, na } = health.checkSummary[c.key];
        const total = ok + fail + unknown;
        const missing = fail + unknown;
        const selected = active === c.key;
        return (
          <div key={c.key} style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <span style={{ width: 130 }}>{c.title}</span>
            <Progress size="small" showInfo={false} percent={total ? Math.round((ok * 100) / total) : 0}
              status={missing ? 'normal' : 'success'} style={{ width: 220, maxWidth: '100%', margin: 0 }} />
            <Typography.Text style={{ width: 56 }}>{ok} de {total}</Typography.Text>
            {specs[c.key]?.severity === 'info' && <Tag>Informativo</Tag>}
            {na > 0 && <Typography.Text type="secondary">No aplica a {na}</Typography.Text>}
            {(missing > 0 || selected) && (
              <Button size="small" type={selected ? 'primary' : 'link'} onClick={() => onSelect(selected ? null : c.key)}>
                {selected ? 'Ver todos' : `Ver los ${missing} que faltan`}
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}
