import js from '@eslint/js';
import globals from 'globals';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';

// Reglas fijadas a propósito: lo recomendado por eslint y por eslint-plugin-react, más las dos reglas de hooks.
// El formato no se revisa. Para sumar reglas, agregarlas acá y corregir lo que aparezca en el mismo PR.
export default [
  { ignores: ['dist'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    plugins: { react, 'react-hooks': reactHooks },
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.browser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: 'detect' } },
    rules: {
      ...react.configs.recommended.rules,
      'no-unused-vars': ['error', { ignoreRestSiblings: true }], // `{ node, ...props }` omite a propósito
      'react/react-in-jsx-scope': 'off', // React 19 con el transform automático de JSX
      'react/prop-types': 'off', // el proyecto no usa PropTypes
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
    },
  },
  { files: ['vite.config.js'], languageOptions: { globals: globals.node } },
];
