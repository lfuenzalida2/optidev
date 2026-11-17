import { theme as antdThemes } from 'antd';

// Design system de Optidev: los colores, radios y tipografía salen de acá,
// no van escritos a mano en los componentes. Los componentes de antd los toman
// vía ConfigProvider (`antdTheme(mode)`); el resto de los estilos inline usa `colors`.
//
// Los neutros (texto, fondos, bordes) cambian con el modo claro/oscuro: en
// `colors` son variables CSS que `applyCssVars(mode)` define en <html>, así
// los estilos inline no tienen que saber en qué modo está el usuario.
//
// Los valores de marca (`brand`) son provisorios hasta tener el manual
// corporativo de Optidev: al cambiarlos acá se actualiza toda la app.

export const brand = {
  primary: '#1890ff',
  dark: '#001529', // barra superior
  accent: '#fa8c16',
};

const neutrals = {
  light: {
    text: '#262626',
    textSecondary: '#595959',
    textMuted: '#8c8c8c',

    pageBg: '#f5f5f5',
    surface: '#ffffff',
    surfaceSubtle: '#fafafa',
    border: '#f0f0f0',
    borderStrong: '#d9d9d9',
    column: '#ebecf0',
    columnDropTarget: '#e6f4ff',
    badgeNeutral: '#bfbfbf',
  },
  dark: {
    text: '#e6e6e6',
    textSecondary: '#a6a6a6',
    textMuted: '#7f7f7f',

    pageBg: '#0f0f0f',
    surface: '#141414',
    surfaceSubtle: '#1f1f1f',
    border: '#303030',
    borderStrong: '#424242',
    column: '#262626',
    columnDropTarget: '#111d2c',
    badgeNeutral: '#595959',
  },
};

const cssVar = (key) => `--md-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;

export const colors = {
  ...brand,
  claude: '#722ed1', // todo lo que hace Claude vía MCP (comentarios, PRs)
  success: '#52c41a',
  warning: '#faad14',
  danger: '#f5222d',
  onDark: '#ffffff', // texto sobre la barra superior (es oscura en ambos modos)

  ...Object.fromEntries(Object.keys(neutrals.light).map((key) => [key, `var(${cssVar(key)})`])),
};

export const applyCssVars = (mode) => {
  const root = document.documentElement;
  Object.entries(neutrals[mode]).forEach(([key, value]) => root.style.setProperty(cssVar(key), value));
  root.style.colorScheme = mode; // scrollbars y controles nativos
};

export const radius = { sm: 4, md: 6, lg: 8 };

export const fontFamily = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";

// Colores que un admin puede asignar a las listas del tablero.
export const LIST_COLORS = [
  '#8c8c8c', '#1890ff', '#13c2c2', '#52c41a', '#faad14', '#fa8c16', '#f5222d', '#eb2f96', '#722ed1',
];

export const antdTheme = (mode) => ({
  algorithm: mode === 'dark' ? antdThemes.darkAlgorithm : antdThemes.defaultAlgorithm,
  token: {
    colorPrimary: brand.primary,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.danger,
    // antd deriva otros tonos de estos: necesita el hex, no la variable CSS.
    colorText: neutrals[mode].text,
    colorBgLayout: neutrals[mode].pageBg,
    borderRadius: radius.md,
    fontFamily,
  },
  components: {
    Layout: { headerBg: brand.dark, headerPadding: '0 20px' },
    Menu: { darkItemBg: brand.dark, darkPopupBg: brand.dark },
  },
});
