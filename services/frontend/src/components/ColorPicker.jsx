import React from 'react';
import { ColorPicker as AntColorPicker } from 'antd';
import { PRESET_COLORS } from '../utils/colors';

// Wrapper para usar dentro de Form.Item: entrega/recibe el color como "#rrggbb".
export default function ColorPicker({ value, onChange }) {
  return (
    <AntColorPicker
      value={value}
      presets={[{ label: 'Sugeridos', colors: PRESET_COLORS }]}
      onChange={(color) => onChange?.(color.toHexString().slice(0, 7))}
      showText
    />
  );
}
