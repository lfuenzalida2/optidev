import React, { useCallback, useEffect, useState } from 'react';
import { App, Button, Card, List, Popconfirm, Select, Space, Table, Typography } from 'antd';
import { ApiOutlined, CloudServerOutlined, CodeOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';

import { CheckStatus, VmModal } from '../components/TestVmsCard';
import { usersApi, vmsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { useTerminals } from '../contexts/TerminalContext';

// Las VMs de todos los usuarios. Un admin entra a cualquiera, las administra y crea la de quien la solicitó.
// Diagnóstico, skills y previews siguen siendo de cada dueño: instalan en la VM un token a nombre de quien las corre.
export default function Vms() {
  const { message } = App.useApp();
  const { openTerminal } = useTerminals();
  const [vms, setVms] = useState([]);
  const [requests, setRequests] = useState([]);
  const [users, setUsers] = useState([]);
  const [newOwnerId, setNewOwnerId] = useState(null);
  const [modal, setModal] = useState(null); // null | { vm } (editar) | { owner } (alta para ese usuario)
  const [checking, setChecking] = useState(null);

  const load = useCallback(async () => {
    try {
      const [allVms, pending] = await Promise.all([vmsApi.all(), vmsApi.requests()]);
      setVms(allVms);
      setRequests(pending);
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }, [message]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    usersApi.list().then(setUsers).catch((err) => message.error(extractErrorMessage(err)));
  }, [message]);

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

  const remove = async (vm) => {
    try {
      await vmsApi.remove(vm.id);
      load();
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  };

  const columns = [
    { title: 'Usuario', key: 'owner', render: (_, vm) => `${vm.owner.name} (${vm.owner.username})` },
    { title: 'Nombre', dataIndex: 'name' },
    {
      title: 'Conexión', key: 'conn',
      render: (_, vm) => <Typography.Text code>{vm.username}@{vm.host}{vm.port !== 22 ? `:${vm.port}` : ''}</Typography.Text>,
    },
    { title: 'Estado', key: 'status', render: (_, vm) => <CheckStatus vm={vm} /> },
    {
      title: '', key: 'actions', width: 250,
      render: (_, vm) => (
        <Space size={4}>
          <Button size="small" type="primary" icon={<CodeOutlined />} onClick={() => openTerminal(vm)}>Terminal</Button>
          <Button size="small" icon={<ApiOutlined />} loading={checking === vm.id} onClick={() => check(vm)}>Probar</Button>
          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => setModal({ vm })} />
          <Popconfirm title="¿Eliminar la VM?" description={`Se borran las credenciales guardadas de ${vm.owner.name}.`}
            onConfirm={() => remove(vm)}>
            <Button size="small" type="text" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  const newOwner = users.find((u) => u.id === newOwnerId);

  return (
    <div style={{ padding: 16 }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {requests.length > 0 && (
          <Card title="Solicitudes de VM">
            <List
              dataSource={requests}
              renderItem={(request) => (
                <List.Item actions={[
                  <Button key="c" type="primary" size="small" icon={<PlusOutlined />}
                    onClick={() => setModal({ owner: request.user })}>Crear VM</Button>,
                ]}>
                  <List.Item.Meta title={`${request.user.name} (${request.user.username})`}
                    description={`Solicitada ${dayjs(request.createdAt).fromNow()}`} />
                </List.Item>
              )}
            />
          </Card>
        )}
        <Card title={<Space><CloudServerOutlined />VMs de optidev</Space>}
          extra={(
            <Space wrap>
              <Select showSearch allowClear placeholder="Crear para el usuario…" style={{ width: 240 }} value={newOwnerId}
                onChange={(id) => setNewOwnerId(id ?? null)} optionFilterProp="label"
                options={users.filter((u) => u.isActive).map((u) => ({ value: u.id, label: `${u.name} (${u.username})` }))} />
              <Button type="primary" icon={<PlusOutlined />} disabled={!newOwner} onClick={() => setModal({ owner: newOwner })}>
                Agregar VM
              </Button>
            </Space>
          )}>
          <Typography.Paragraph type="secondary">
            Las VMs de todos los usuarios. Un admin puede abrir la terminal, probar, editar y eliminar cualquiera.
            El diagnóstico, las skills y las previews las corre cada usuario desde su perfil.
          </Typography.Paragraph>
          <Table rowKey="id" size="small" columns={columns} dataSource={vms} pagination={false} scroll={{ x: 'max-content' }} />
        </Card>
      </Space>
      <VmModal open={Boolean(modal)} vm={modal?.vm} owner={modal?.owner} onClose={() => setModal(null)}
        onSaved={() => { setModal(null); setNewOwnerId(null); load(); }} />
    </div>
  );
}
