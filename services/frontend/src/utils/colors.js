// Paleta para repos (los mismos tonos que los tags de antd).
export const PRESET_COLORS = [
  '#1890ff', '#13c2c2', '#52c41a', '#a0d911', '#faad14', '#fa8c16',
  '#f5222d', '#eb2f96', '#722ed1', '#2f54eb', '#8c8c8c', '#262626',
];

// Texto blanco o negro según la luminancia del fondo.
export const textOn = (hex) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return '#fff';
  const n = parseInt(m[1], 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? '#262626' : '#fff';
};
