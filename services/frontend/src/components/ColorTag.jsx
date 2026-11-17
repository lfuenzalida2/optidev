import React from 'react';
import { Tag } from 'antd';
import { textOn } from '../utils/colors';

export default function ColorTag({ color, children, style, ...rest }) {
  return (
    <Tag color={color} style={{ color: textOn(color), marginInlineEnd: 4, ...style }} {...rest}>
      {children}
    </Tag>
  );
}
