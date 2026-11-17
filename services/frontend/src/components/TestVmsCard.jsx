import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert, App, Button, Card, Checkbox, Form, Input, InputNumber, Modal, Popconfirm, Radio, Space, Table, Tag,
  Tooltip, Typography,
} from 'antd';
import {
  ApiOutlined, CloudServerOutlined, CodeOutlined, DeleteOutlined, EditOutlined, MedicineBoxOutlined, PlusOutlined, SendOutlined, SyncOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';

import { vmsApi } from '../api';
import { useAuth } from '../contexts/AuthContext';
import { extractErrorMessage } from '../api/axios';
import { useTerminals } from '../contexts/TerminalContext';
import VmDoctor from './VmDoctor';

export function CheckStatus({ vm }) {
  if (vm.lastCheckOk == null) return <Typography.Text type="secondary">Sin probar</Typography.Text>;
  return (
    <Tooltip title={`${vm.lastCheckMessage} · ${dayjs(vm.lastCheckAt).fromNow()}`}>
      <Tag color={vm.lastCheckOk ? 'green' : 'red'} style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
        {vm.lastCheckOk ? 'Conecta' : vm.lastCheckMessage}
      </Tag>
    </Tooltip>
  );
}

// Alta/edición. Con `owner` (un admin desde la vista de VMs) la VM se crea para ese usuario. Al editar, los campos del secreto vacíos conservan el guardado
// (el backend nunca lo devuelve, así que no hay nada que precargar).
export function VmModal({ vm, open, onClose, onSaved, owner = null }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const authType = Form.useWatch('authType', form);
  const editing = Boolean(vm);
  const keepSecret = editing && authType === vm?.authType;

  useEffect(() => {
    if (!open) return;
    setTestResult(null);
    form.resetFields();
    form.setFieldsValue(vm
      ? { name: vm.name, host: vm.host, port: vm.port, username: vm.username, authType: vm.authType }
      : { name: 'Mi VM de pruebas', port: 22, authType: 'password' });
  }, [open, vm, form]);

  // Sin el secreto vacío: en una edición, vacío = conservar el guardado.
  const bodyFrom = (values) => {
    const body = { ...values };
    ['password', 'privateKey', 'passphrase'].forEach((k) => { if (!body[k]) delete body[k]; });
    return body;
  };

  // No bloquea el guardado: solo le dice al usuario si optidev llega con esos datos.
  const test = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await vmsApi.test(bodyFrom(form.getFieldsValue()), vm?.id));
    } catch (err) {
      setTestResult({ ok: false, message: extractErrorMessage(err) });
    } finally {
      setTesting(false);
    }
  };

  const submit = async (values) => {
    setSaving(true);
    try {
      const body = bodyFrom(values);
      if (editing) await vmsApi.update(vm.id, body);
      else await vmsApi.create(owner ? { ...body, userId: owner.id } : body);
      message.success(editing ? 'VM actualizada' : 'VM guardada');
      onSaved();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const secretRule = keepSecret ? [] : [{ required: true, message: 'Obligatorio' }];
  return (
    <Modal open={open} title={editing ? `Editar ${vm.name}` : `Agregar VM de pruebas${owner ? ` para ${owner.name}` : ''}`} onCancel={onClose}
      destroyOnHidden width={560}
      footer={(
        <div style={{ display: 'flex', gap: 8 }}>
          <Button icon={<ApiOutlined />} loading={testing} onClick={test}>Probar conexión</Button>
          <span style={{ flex: 1 }} />
          <Button onClick={onClose}>Cancelar</Button>
          <Button type="primary" loading={saving} onClick={() => form.submit()}>Guardar</Button>
        </div>
      )}>
      <Form form={form} layout="vertical" onFinish={submit} autoComplete="off">
        <Form.Item name="name" label="Nombre"><Input maxLength={100} /></Form.Item>
        <div style={{ display: 'flex', gap: 12 }}>
          <Form.Item name="host" label="IP o host" style={{ flex: 1 }}
            rules={[{ required: true, whitespace: true, message: 'Obligatorio' }]}>
            <Input placeholder="10.0.20.15" />
          </Form.Item>
          <Form.Item name="port" label="Puerto SSH" style={{ width: 120 }} rules={[{ required: true, message: ' ' }]}>
            <InputNumber min={1} max={65535} style={{ width: '100%' }} />
          </Form.Item>
        </div>
        <Form.Item name="username" label="Usuario" rules={[{ required: true, whitespace: true, message: 'Obligatorio' }]}>
          <Input placeholder="root" autoComplete="off" />
        </Form.Item>
        <Form.Item name="authType" label="Autenticación">
          <Radio.Group optionType="button" options={[
            { value: 'password', label: 'Contraseña' }, { value: 'key', label: 'Clave privada' },
          ]} />
        </Form.Item>
        {authType === 'password' ? (
          <Form.Item name="password" label="Contraseña" rules={secretRule}>
            <Input.Password autoComplete="new-password" placeholder={keepSecret ? 'Dejar vacío para mantener la actual' : ''} />
          </Form.Item>
        ) : (
          <>
            <Form.Item name="privateKey" label="Clave privada (formato OpenSSH o PEM)" rules={secretRule}>
              <Input.TextArea autoSize={{ minRows: 4, maxRows: 10 }} spellCheck={false}
                style={{ fontFamily: 'monospace', fontSize: 12 }}
                placeholder={keepSecret ? 'Dejar vacío para mantener la actual' : '-----BEGIN OPENSSH PRIVATE KEY-----'} />
            </Form.Item>
            <Form.Item name="passphrase" label="Passphrase de la clave (si tiene)">
              <Input.Password autoComplete="new-password" />
            </Form.Item>
          </>
        )}
        {editing && vm.hostKeyFingerprint && (
          <Form.Item name="resetHostKey" valuePropName="checked"
            extra={<>Clave guardada: <Typography.Text code>{vm.hostKeyFingerprint}</Typography.Text>. Marcalo solo si recreaste la VM.</>}>
            <Checkbox>Olvidar clave del host</Checkbox>
          </Form.Item>
        )}
      </Form>
      {testResult && (
        <Alert type={testResult.ok ? 'success' : 'error'} showIcon closable onClose={() => setTestResult(null)}
          message={testResult.ok ? `Conecta: ${testResult.message}` : testResult.message}
          description={testResult.hostKeyFingerprint
            && <>Clave del host: <Typography.Text code>{testResult.hostKeyFingerprint}</Typography.Text></>} />
      )}
    </Modal>
  );
}

export default function TestVmsCard() {
  const { message } = App.useApp();
  const { openTerminal } = useTerminals();
  const { isAdmin } = useAuth();
  const [vms, setVms] = useState([]);
  const [vmRequest, setVmRequest] = useState(null); // la solicitud de VM abierta del usuario, si hay
  const [modal, setModal] = useState(null); // null | { vm } (vm null = alta)
  const [checking, setChecking] = useState(null);
  const [doctorVm, setDoctorVm] = useState(null);
  const [agentsVm, setAgentsVm] = useState(null);
  const [agentsStatus, setAgentsStatus] = useState({}); // vmId → 'ok' | 'missing' | 'outdated' | 'no-timer'

  const load = useCallback(async () => {
    try {
      setVms(await vmsApi.list());
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (!isAdmin) vmsApi.getRequest().then(setVmRequest).catch(() => {});
  }, [isAdmin]);

  const requestVm = async () => {
    try {
      setVmRequest(await vmsApi.request());
      message.success('Solicitud enviada a los admins');
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  // Estado de agentes y skills en cada VM (un diagnóstico por SSH). Si la VM no responde no se avisa nada:
  // eso ya lo muestra la columna Estado.
  const loadAgentsStatus = useCallback(async (list) => {
    const entries = await Promise.all(list.map(async (vm) => {
      try {
        const { checks } = await vmsApi.diagnose(vm.id, 'agents');
        const ok = Object.fromEntries(checks.map((c) => [c.id, c.ok]));
        if (!ok.agents_marketplace) return [vm.id, 'missing'];
        if (!ok.agents_current) return [vm.id, 'outdated'];
        return [vm.id, ok.agents_timer ? 'ok' : 'no-timer'];
      } catch {
        return [vm.id, null];
      }
    }));
    setAgentsStatus(Object.fromEntries(entries));
  }, []);

  useEffect(() => { if (vms.length) loadAgentsStatus(vms); }, [vms, loadAgentsStatus]);

  const check = async (vm) => {
    setChecking(vm.id);
    try {
      const result = await vmsApi.check(vm.id);
      if (result.lastCheckOk) message.success(`Conectado: ${result.lastCheckMessage}`);
      else message.error(result.lastCheckMessage);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setChecking(null);
    }
  };

  const columns = [
    { title: 'Nombre', dataIndex: 'name' },
    {
      title: 'Conexión', key: 'conn',
      render: (_, vm) => <Typography.Text code>{vm.username}@{vm.host}{vm.port !== 22 ? `:${vm.port}` : ''}</Typography.Text>,
    },
    { title: 'Autenticación', dataIndex: 'authType', render: (t) => (t === 'key' ? 'Clave privada' : 'Contraseña') },
    { title: 'Estado', key: 'status', render: (_, vm) => <CheckStatus vm={vm} /> },
    {
      title: '', key: 'actions', width: 370,
      render: (_, vm) => (
        <Space size={4}>
          <Button size="small" type="primary" icon={<CodeOutlined />} onClick={() => openTerminal(vm)}>Terminal</Button>
          <Button size="small" icon={<ApiOutlined />} loading={checking === vm.id} onClick={() => check(vm)}>Probar</Button>
          <Tooltip title="Diagnosticar problemas conocidos (Docker, git, disco…)">
            <Button size="small" type="text" icon={<MedicineBoxOutlined />} onClick={() => setDoctorVm(vm)} />
          </Tooltip>
          <Tooltip title="Agentes y skills de Optidev: instalar o actualizar ahora (se actualizan solos cada noche)">
            <Button size="small" type="text" icon={<SyncOutlined />} onClick={() => setAgentsVm(vm)} />
          </Tooltip>
          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => setModal({ vm })} />
          <Popconfirm title="¿Eliminar la VM?" description="Se borran las credenciales guardadas."
            onConfirm={async () => { await vmsApi.remove(vm.id); load(); }}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card title={<Space><CloudServerOutlined />Mi VM de pruebas</Space>}
      extra={(
        <Space>
          {!isAdmin && (
            <Button size="small" icon={<SendOutlined />} disabled={Boolean(vmRequest)} onClick={requestVm}>
              {vmRequest ? 'Solicitud enviada' : 'Solicitar VM'}
            </Button>
          )}
          <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setModal({ vm: null })}>Agregar</Button>
        </Space>
      )}>
      <Typography.Paragraph>
        La VM donde optidev carga las ramas de los PRs para que las pruebes. Cargá la IP y las credenciales SSH
        y usá <b>Probar</b> para verificar que optidev llega. Con <b>Terminal</b> abrís una consola SSH
        que sigue abierta mientras navegás por optidev.
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary">
        La contraseña o la clave se guardan cifradas y no se vuelven a mostrar. Solo vos ves tus VMs.
      </Typography.Paragraph>
      {vms.length === 0 && (
        <Alert type="info" showIcon style={{ marginBottom: 12 }} message="Todavía no cargaste tu VM de pruebas." />
      )}
      {vms.filter((vm) => ['missing', 'outdated', 'no-timer'].includes(agentsStatus[vm.id])).map((vm) => (
        <Alert key={vm.id} showIcon style={{ marginBottom: 12 }}
          type={agentsStatus[vm.id] === 'no-timer' ? 'info' : 'warning'}
          message={{
            missing: `Las skills y agentes de Optidev no están instalados en «${vm.name}»`,
            outdated: `Las skills y agentes de Optidev están desactualizados en «${vm.name}»`,
            'no-timer': `«${vm.name}» no tiene la actualización automática de skills y agentes`,
          }[agentsStatus[vm.id]]}
          description={agentsStatus[vm.id] === 'missing'
            ? 'Sin ellos, skills como revisar-pr no funcionan en esa VM.'
            : agentsStatus[vm.id] === 'outdated'
              ? 'Pueden faltar skills o correcciones nuevas.' : 'Se actualizarán solo si las actualizás a mano.'}
          action={<Button size="small" type="primary" onClick={() => setAgentsVm(vm)}>
            {agentsStatus[vm.id] === 'missing' ? 'Instalar' : agentsStatus[vm.id] === 'outdated' ? 'Actualizar' : 'Configurar'}
          </Button>} />
      ))}
      {vms.length > 0 && (
        <Table rowKey="id" size="small" columns={columns} dataSource={vms} pagination={false} scroll={{ x: 'max-content' }} />
      )}
      <Modal open={Boolean(doctorVm)} title="Diagnóstico de la VM" footer={null} width={680} destroyOnHidden
        onCancel={() => setDoctorVm(null)}>
        {doctorVm && <VmDoctor vm={doctorVm} />}
      </Modal>
      <Modal open={Boolean(agentsVm)} title="Agentes y skills de Optidev" footer={null} width={680} destroyOnHidden
        onCancel={() => { setAgentsVm(null); loadAgentsStatus(vms); }}>
        {agentsVm && (
          <>
            <Typography.Paragraph type="secondary">
              La VM actualiza sola el marketplace optidev-agents todas las noches (0:30, hora de Chile). Si
              necesitás una skill o agente nuevo antes, usá <b>Instalar / actualizar ahora</b>. Claude tiene que
              reiniciarse para tomar los cambios (sesiones abiertas).
            </Typography.Paragraph>
            <VmDoctor vm={agentsVm} group="agents" steps title={`Agentes y skills en ${agentsVm.name}`}
              doneMessage="Agentes y skills instalados y con actualización nocturna" alwaysFix={['agents_marketplace']} />
          </>
        )}
      </Modal>
      <VmModal open={Boolean(modal)} vm={modal?.vm} onClose={() => setModal(null)}
        onSaved={() => { setModal(null); load(); }} />
    </Card>
  );
}
