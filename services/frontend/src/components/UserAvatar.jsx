import React from 'react';
import { Avatar } from 'antd';

import { colors } from '../theme';
import { textOn } from '../utils/colors';

// "Felipe Maturana" -> "FM"; con un solo nombre, solo su inicial.
export function initials(name = '') {
  const [first, second] = name.trim().split(/\s+/);
  return `${first?.[0] ?? ''}${second?.[0] ?? ''}`.toUpperCase();
}

// Avatar de un usuario: su foto si subió una; si no, sus iniciales sobre el
// color que eligió en su perfil (o el primario del tema).
export default function UserAvatar({ user, size = 'default', style, ...rest }) {
  const background = user?.avatarColor || colors.primary;
  const fontSize = typeof size === 'number' ? Math.max(10, Math.round(size * 0.45)) : undefined;
  return (
    <Avatar
      size={size}
      src={user?.avatarUrl || undefined}
      style={{ background, color: textOn(background), fontSize, ...style }}
      {...rest}
    >
      {initials(user?.name)}
    </Avatar>
  );
}
