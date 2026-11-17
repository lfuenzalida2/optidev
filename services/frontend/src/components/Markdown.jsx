import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components = {
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
};

// `overrides`: componentes propios por tag (p. ej. los links relativos del explorador de Workflow).
export default function Markdown({ children, overrides }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={overrides ? { ...components, ...overrides } : components}>
        {children || ''}
      </ReactMarkdown>
    </div>
  );
}
