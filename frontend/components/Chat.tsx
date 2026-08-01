"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { ask, type Source } from "../lib/api";

type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
};

const SUGGESTIONS = [
  "¿De qué tratan mis documentos?",
  "Resume el último documento que subí",
];

function Avatar({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-sm">
      {children}
    </div>
  );
}

function SourcesList({ sources }: { sources: Source[] }) {
  return (
    <div className="mt-2 space-y-1 border-t border-slate-100 pt-2">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        Fuentes
      </p>
      {sources.map((s, j) => (
        <details key={j} className="rounded-lg bg-slate-50 text-xs">
          <summary className="cursor-pointer px-2 py-1.5 text-slate-600 hover:text-blue-600">
            📄 {s.filename}{" "}
            <span className="text-slate-400">· dist {s.distance.toFixed(3)}</span>
          </summary>
          <p className="px-2 pb-2 whitespace-pre-wrap text-slate-500">{s.content}</p>
        </details>
      ))}
    </div>
  );
}

export default function Chat({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || loading) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: question }]);
    setLoading(true);
    try {
      const res = await ask(question);
      setMessages((m) => [
        ...m,
        { role: "assistant", content: res.answer, sources: res.sources },
      ]);
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: `⚠️ Error: ${e instanceof Error ? e.message : "desconocido"}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center gap-2">
          <button
            onClick={onToggleSidebar}
            className="rounded-lg p-2 hover:bg-slate-100 md:hidden"
            aria-label="Abrir documentos"
          >
            ☰
          </button>
          <h2 className="text-sm font-semibold">Chat</h2>
          <span className="hidden text-xs text-slate-400 sm:inline">
            cada pregunta es independiente (single-turn)
          </span>
        </div>
        {messages.length > 0 && (
          <button
            onClick={() => setMessages([])}
            className="rounded-lg px-3 py-1.5 text-xs text-slate-500 hover:bg-slate-100"
          >
            Limpiar
          </button>
        )}
      </header>

      <div className="chat-scroll flex-1 overflow-y-auto px-4 py-6">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
            <span className="text-5xl">💬</span>
            <div>
              <p className="text-lg font-medium text-slate-700">
                Pregunta a tus documentos
              </p>
              <p className="mt-1 text-sm text-slate-400">
                Sube un PDF, TXT, MD o IPYNB en la barra lateral y haz tu primera
                pregunta.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="rounded-full border border-slate-300 bg-white px-4 py-1.5 text-sm text-slate-600 transition hover:border-blue-400 hover:text-blue-600"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {messages.map((m, i) => (
              <div
                key={i}
                className={`flex gap-3 ${m.role === "user" ? "justify-end" : "justify-start"}`}
              >
                {m.role === "assistant" && <Avatar>🤖</Avatar>}
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[80%] rounded-2xl rounded-br-md bg-blue-600 px-4 py-2.5 text-sm text-white shadow-sm"
                      : "max-w-[80%] rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm"
                  }
                >
                  <p className="whitespace-pre-wrap">{m.content}</p>
                  {m.sources && m.sources.length > 0 && (
                    <SourcesList sources={m.sources} />
                  )}
                </div>
                {m.role === "user" && <Avatar>🧑</Avatar>}
              </div>
            ))}
            {loading && (
              <div className="flex gap-3">
                <Avatar>🤖</Avatar>
                <div className="rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3 shadow-sm">
                  <span className="flex gap-1">
                    <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
                    <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
                    <span className="typing-dot h-2 w-2 rounded-full bg-slate-400" />
                  </span>
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="border-t border-slate-200 bg-white px-4 py-3">
        <div className="mx-auto flex max-w-3xl items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            placeholder="Escribe tu pregunta… (Enter para enviar)"
            rows={1}
            className="max-h-32 flex-1 resize-none rounded-2xl border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
          />
          <button
            onClick={() => send()}
            disabled={loading || !input.trim()}
            className="rounded-2xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700 disabled:bg-slate-300"
          >
            ➤
          </button>
        </div>
      </div>
    </>
  );
}
