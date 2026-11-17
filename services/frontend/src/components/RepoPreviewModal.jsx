import React, { useEffect, useState } from 'react';
import { App, Form, Input, Modal, Typography } from 'antd';

import { reposApi } from '../api';
import { extractErrorMessage } from '../api/axios';

const RECIPE_PLACEHOLDER = `# Corre en la VM, dentro del checkout de la rama (como el usuario SSH).\n# Ejemplo:\ndocker compose up -d --build`;

// Receta para cargar una rama en las VMs de pruebas. Solo admins: corre en las VMs de todos.
export default function RepoPreviewModal({ repo, onClose, onSaved }) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (repo) form.setFieldsValue({ script: repo.previewScript || '', url: repo.previewUrl || '' });
  }, [repo, form]);

  const save = async ({ script, url }) => {
    setSaving(true);
    try {
      await reposApi.setPreview(repo.id, { script, url });
      message.success('Receta guardada');
      onSaved();
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={Boolean(repo)} title={repo && `Cargar ${repo.key} en las VMs de pruebas`} okText="Guardar"
      okButtonProps={{ loading: saving }} onOk={() => form.submit()} onCancel={onClose} destroyOnHidden width={720}>
      <Typography.Paragraph type="secondary">
        Al apretar <b>Cargar en mi VM</b> en Testing, optidev descarga la rama, la deja en
        <Typography.Text code>/opt/optidev-preview/{repo?.key}</Typography.Text> de la VM y corre esta receta ahí
        (bash, se corta en el primer error). Variables disponibles: <Typography.Text code>PREVIEW_BRANCH</Typography.Text>{' '}
        <Typography.Text code>PREVIEW_SHA</Typography.Text> <Typography.Text code>PREVIEW_DIR</Typography.Text>{' '}
        <Typography.Text code>PREVIEW_HOST</Typography.Text> <Typography.Text code>PREVIEW_REPO</Typography.Text>.
        No pongas secretos acá.
      </Typography.Paragraph>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="script" label="Receta" extra="Vacía = el repo no se puede cargar en las VMs.">
          <Input.TextArea autoSize={{ minRows: 8, maxRows: 24 }} spellCheck={false}
            style={{ fontFamily: 'monospace', fontSize: 12 }} placeholder={RECIPE_PLACEHOLDER} />
        </Form.Item>
        <Form.Item name="url" label="Link para abrir lo cargado" extra={<>{'{host}'} se reemplaza por el host de la VM.</>}>
          <Input placeholder="http://{host}:8080" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
