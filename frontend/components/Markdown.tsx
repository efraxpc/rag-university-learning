"use client";

// Renderiza las respuestas del asistente en Markdown:
// - Bloques de código → vista de código (resaltado Prism + botón copiar).
// - Conceptos → vista de texto estructurado (encabezados, listas, negritas,
//   citas, tablas GFM).

import { isValidElement, useState, type ReactElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { PrismLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import python from "react-syntax-highlighter/dist/esm/languages/prism/python";
import javascript from "react-syntax-highlighter/dist/esm/languages/prism/javascript";
import typescript from "react-syntax-highlighter/dist/esm/languages/prism/typescript";
import jsx from "react-syntax-highlighter/dist/esm/languages/prism/jsx";
import tsx from "react-syntax-highlighter/dist/esm/languages/prism/tsx";
import bash from "react-syntax-highlighter/dist/esm/languages/prism/bash";
import json from "react-syntax-highlighter/dist/esm/languages/prism/json";
import sql from "react-syntax-highlighter/dist/esm/languages/prism/sql";
import css from "react-syntax-highlighter/dist/esm/languages/prism/css";
import markup from "react-syntax-highlighter/dist/esm/languages/prism/markup";
import markdownLang from "react-syntax-highlighter/dist/esm/languages/prism/markdown";
import yaml from "react-syntax-highlighter/dist/esm/languages/prism/yaml";
import java from "react-syntax-highlighter/dist/esm/languages/prism/java";
import c from "react-syntax-highlighter/dist/esm/languages/prism/c";
import cpp from "react-syntax-highlighter/dist/esm/languages/prism/cpp";
import go from "react-syntax-highlighter/dist/esm/languages/prism/go";
import rust from "react-syntax-highlighter/dist/esm/languages/prism/rust";

const LANGUAGES: Record<string, [string, unknown]> = {
  python: ["Python", python],
  javascript: ["JavaScript", javascript],
  js: ["JavaScript", javascript],
  typescript: ["TypeScript", typescript],
  ts: ["TypeScript", typescript],
  jsx: ["JSX", jsx],
  tsx: ["TSX", tsx],
  bash: ["Bash", bash],
  sh: ["Bash", bash],
  shell: ["Bash", bash],
  json: ["JSON", json],
  sql: ["SQL", sql],
  css: ["CSS", css],
  html: ["HTML", markup],
  xml: ["XML", markup],
  markdown: ["Markdown", markdownLang],
  md: ["Markdown", markdownLang],
  yaml: ["YAML", yaml],
  yml: ["YAML", yaml],
  java: ["Java", java],
  c: ["C", c],
  cpp: ["C++", cpp],
  "c++": ["C++", cpp],
  go: ["Go", go],
  rust: ["Rust", rust],
};

for (const [id, [, impl]] of Object.entries(LANGUAGES)) {
  SyntaxHighlighter.registerLanguage(id, impl as never);
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const entry = LANGUAGES[language.toLowerCase()];

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // portapapeles no disponible (contexto no seguro): ignorar
    }
  }

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-slate-700 bg-[#282c34] text-xs">
      <div className="flex items-center justify-between border-b border-slate-700 bg-slate-800/70 px-3 py-1.5">
        <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-slate-400">
          {entry ? entry[0] : language || "código"}
        </span>
        <button
          onClick={copy}
          className="text-[11px] text-slate-400 transition hover:text-white"
        >
          {copied ? "✓ Copiado" : "Copiar"}
        </button>
      </div>
      {entry ? (
        <SyntaxHighlighter
          language={language.toLowerCase()}
          style={oneDark}
          PreTag="div"
          customStyle={{
            margin: 0,
            background: "transparent",
            padding: "0.75rem 1rem",
            fontSize: "12px",
            lineHeight: "1.6",
          }}
        >
          {code}
        </SyntaxHighlighter>
      ) : (
        // Lenguaje no registrado: mismo aspecto, sin resaltado.
        <pre className="overflow-x-auto px-4 py-3 font-mono text-[12px] leading-relaxed text-slate-100">
          <code>{code}</code>
        </pre>
      )}
    </div>
  );
}

function PreBlock({ children }: { children?: ReactNode }) {
  // En Markdown un <pre> siempre envuelve un <code> (bloque fenced).
  if (isValidElement(children)) {
    const el = children as ReactElement<{
      className?: string;
      children?: ReactNode;
    }>;
    const language = /language-(\S+)/.exec(el.props.className ?? "")?.[1] ?? "";
    const code = String(el.props.children ?? "").replace(/\n$/, "");
    return <CodeBlock language={language} code={code} />;
  }
  return <pre>{children}</pre>;
}

const COMPONENTS: Components = {
  pre: PreBlock,
  // A estas alturas <code> solo puede ser inline (los bloques los cubre pre).
  code: ({ children }) => (
    <code className="rounded border border-slate-200 bg-slate-100 px-1 py-0.5 font-mono text-[0.85em] text-slate-800">
      {children}
    </code>
  ),
  h1: ({ children }) => (
    <h1 className="mb-2 mt-4 text-base font-bold first:mt-0">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-2 mt-4 text-base font-bold first:mt-0">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-1.5 mt-3 text-sm font-bold first:mt-0">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="mb-1 mt-3 text-sm font-semibold first:mt-0">{children}</h4>
  ),
  p: ({ children }) => (
    <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-2 ml-4 list-disc space-y-1">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 ml-4 list-decimal space-y-1">{children}</ol>
  ),
  li: ({ children }) => (
    <li className="leading-relaxed marker:text-slate-400">{children}</li>
  ),
  strong: ({ children }) => (
    <strong className="font-semibold text-slate-900">{children}</strong>
  ),
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-blue-600 underline underline-offset-2 hover:text-blue-800"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 rounded-r-lg border-l-4 border-blue-200 bg-blue-50 px-3 py-1.5 text-slate-600">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-slate-200" />,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-slate-200 bg-slate-50 px-2 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-slate-200 px-2 py-1 align-top">{children}</td>
  ),
};

export default function Markdown({ content }: { content: string }) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={COMPONENTS}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
