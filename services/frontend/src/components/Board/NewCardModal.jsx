import React, { useState } from 'react';
import { App, Form, Input, Modal, Select } from 'antd';

import { cardsApi } from '../../api';
import { extractErrorMessage } from '../../api/axios';
import MarkdownEditor from '../MarkdownEditor';

// Al crear una tarjeta se exige descripción y al menos un repositorio; el
// responsable queda por defecto en quien la crea (lo asigna el backend).
export default function NewCardModal({ list, repositories, onClose, onCreated }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  const submit = async (values) => {
    setSaving(true);
    try {
      await cardsApi.create({ ...values, listId: list.id });
      onCreated();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open
      title={`Nueva tarjeta en "${list.name}"`}
      okText="Crear"
      confirmLoading={saving}
      onOk={form.submit}
      onCancel={onClose}
    >
      <Form form={form} layout="vertical" onFinish={submit}>
        <Form.Item name="title" label="Título" rules={[{ required: true, whitespace: true, message: 'Ingresá un título' }]}>
          <Input autoFocus maxLength={255} />
        </Form.Item>
        <Form.Item
          name="description" label="Descripción"
          rules={[{ required: true, whitespace: true, message: 'Ingresá una descripción' }]}
        >
          <MarkdownEditor autoSize={{ minRows: 4, maxRows: 16 }} placeholder="Qué hay que hacer" />
        </Form.Item>
        <Form.Item
          name="repositories" label="Repositorios"
          rules={[{ required: true, type: 'array', min: 1, message: 'Elegí al menos un repositorio' }]}
        >
          <Select
            mode="multiple" placeholder="Elegí uno o más"
            options={repositories.map((r) => ({ value: r.key, label: r.key }))}
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
