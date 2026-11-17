import React, { useRef, useState } from 'react';
import { App, Image, Input, Space, Spin, Typography } from 'antd';

import { uploadsApi } from '../api';
import { extractErrorMessage } from '../api/axios';
import { colors, radius } from '../theme';

const imageFiles = (dataTransfer) => [...(dataTransfer?.files || [])].filter((f) => f.type.startsWith('image/'));

// Mismas URLs que reconoce el backend (upload_service._REFERENCE).
const UPLOAD_URL = /\/api\/uploads\/[0-9a-f]{32}\.(?:png|jpg|gif|webp)/g;
const referencedImages = (text) => [...new Set(text.match(UPLOAD_URL) || [])];

// TextArea de markdown que acepta imágenes pegadas (Ctrl+V) o arrastradas: las
// sube y escribe `![imagen](url)` donde estaba el cursor. Sirve suelto o dentro
// de un Form.Item (value/onChange). Debajo muestra miniaturas de las imágenes
// que tiene el texto (click = ver en grande).
export default function MarkdownEditor({ value = '', onChange, hint = true, ...rest }) {
  const { message } = App.useApp();
  const ref = useRef(null);
  const [uploading, setUploading] = useState(0);
  const images = referencedImages(value);

  const insertImages = async (files) => {
    const textarea = ref.current?.resizableTextArea?.textArea;
    const start = textarea?.selectionStart ?? value.length;
    const end = textarea?.selectionEnd ?? value.length;
    setUploading((n) => n + files.length);
    const snippets = [];
    for (const file of files) {
      try {
        const { url } = await uploadsApi.image(file);
        snippets.push(`![${file.name && file.name !== 'image.png' ? file.name : 'imagen'}](${url})`);
      } catch (err) {
        message.error(`No se pudo subir la imagen: ${extractErrorMessage(err)}`);
      } finally {
        setUploading((n) => n - 1);
      }
    }
    if (!snippets.length) return;
    const before = value.slice(0, start);
    const text = `${before && !before.endsWith('\n') ? '\n' : ''}${snippets.join('\n')}\n`;
    onChange?.({ target: { value: before + text + value.slice(end) } });
  };

  const onPaste = (e) => {
    const files = imageFiles(e.clipboardData);
    if (!files.length) return;
    e.preventDefault();
    insertImages(files);
  };

  const onDrop = (e) => {
    const files = imageFiles(e.dataTransfer);
    if (!files.length) return;
    e.preventDefault();
    insertImages(files);
  };

  return (
    <Spin spinning={uploading > 0} tip="Subiendo imagen…">
      <Input.TextArea ref={ref} value={value} onChange={onChange} onPaste={onPaste} onDrop={onDrop} {...rest} />
      {images.length > 0 && (
        <Image.PreviewGroup>
          <Space wrap size={6} style={{ margin: '6px 0' }}>
            {images.map((url) => (
              <Image key={url} src={url} width={72} height={72}
                style={{ objectFit: 'cover', borderRadius: radius.sm, border: `1px solid ${colors.border}` }} />
            ))}
          </Space>
        </Image.PreviewGroup>
      )}
      {hint && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          Markdown soportado. Pegá o arrastrá imágenes para adjuntarlas.
        </Typography.Text>
      )}
    </Spin>
  );
}
