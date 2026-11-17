import '@ant-design/v5-patch-for-react-19';
import React, { useLayoutEffect, useMemo } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { App as AntApp, ConfigProvider } from 'antd';
import esES from 'antd/locale/es_ES';
import dayjs from 'dayjs';
import 'dayjs/locale/es';
import relativeTime from 'dayjs/plugin/relativeTime';

import App from './App';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import './index.css';
import { antdTheme, applyCssVars } from './theme';

dayjs.extend(relativeTime);
dayjs.locale('es');

// El modo claro/oscuro es una preferencia de cada usuario (se guarda en el backend).
function ThemedApp({ children }) {
  const { user } = useAuth();
  const mode = user?.darkMode ? 'dark' : 'light';
  useLayoutEffect(() => applyCssVars(mode), [mode]);
  const theme = useMemo(() => antdTheme(mode), [mode]);
  return (
    <ConfigProvider locale={esES} theme={theme}>
      {/* AntApp agrega un <div>: sin altura, el 100% del Layout no llega a la ventana. */}
      <AntApp style={{ height: '100%' }}>
        {children}
      </AntApp>
    </ConfigProvider>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthProvider>
      <ThemedApp>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </ThemedApp>
    </AuthProvider>
  </React.StrictMode>
);
