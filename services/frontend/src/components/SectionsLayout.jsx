import React from 'react';
import { useSearchParams } from 'react-router-dom';
import { Grid, Layout, Menu, Select, Space, Typography } from 'antd';

// Página con sidebar: cada cosa es una sección ({ key, icon, label, Component }); la elegida queda
// en la URL (?seccion=listas) para poder linkearla y que sobreviva al recargar.
// `header` se muestra sobre la sección elegida.
export default function SectionsLayout({ title, sections, header }) {
  const [params, setParams] = useSearchParams();
  const screens = Grid.useBreakpoint();
  const section = sections.find((s) => s.key === params.get('seccion')) || sections[0];
  const { Component } = section;

  const menuItems = sections.map(({ key, icon, label }) => ({ key, icon, label }));
  const select = ({ key }) => setParams({ seccion: key }, { replace: true });

  // En pantallas angostas el sidebar no entra: pasa a ser un selector arriba del contenido
  // (un Menu horizontal escondería la sección elegida detrás del "...").
  if (screens.md === false) {
    return (
      <div style={{ padding: 12 }}>
        <Select value={section.key} onChange={(key) => select({ key })} style={{ width: '100%', marginBottom: 12 }}
          aria-label={`Sección de ${title}`}
          options={sections.map(({ key, icon, label }) => ({ value: key, label: <Space>{icon}{label}</Space> }))} />
        {header && <div style={{ marginBottom: 12 }}>{header}</div>}
        <Component />
      </div>
    );
  }

  return (
    <Layout style={{ flex: '1 0 auto' }}>
      <Layout.Sider theme="light" width={230}>
        {/* Sticky: el menú queda a la vista aunque la sección sea larga. */}
        <div style={{ position: 'sticky', top: 0 }}>
          <Typography.Title level={5} style={{ margin: 0, padding: '16px 24px 8px' }}>{title}</Typography.Title>
          <Menu mode="inline" items={menuItems} selectedKeys={[section.key]} onClick={select}
            style={{ borderInlineEnd: 'none' }} />
        </div>
      </Layout.Sider>
      <Layout.Content style={{ padding: 16, minWidth: 0 }}>
        <div style={{ maxWidth: 1100 }}>
          {header && <div style={{ marginBottom: 16 }}>{header}</div>}
          <Component />
        </div>
      </Layout.Content>
    </Layout>
  );
}
