"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { listDocuments, uploadDocument, deleteDocument, type Doc } from "../lib/api";

const STATUS: Record<Doc["status"], { label: string; classes: string; pulse?: boolean }> = {
  ready: { label: "ready", classes: "bg-emerald-100 text-emerald-700" },
  pending: { label: "procesando", classes: "bg-amber-100 text-amber-700", pulse: true },
  error: { label: "error", classes: "bg-rose-100 text-rose-700" },
};

export default function Sidebar() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
    } catch {
      // API aún no disponible; se reintenta al pulsar "actualizar".
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-refresco mientras haya documentos procesándose.
  useEffect(() => {
    if (!docs.some((d) => d.status === "pending")) return;
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [docs, refresh]);

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadDocument(file);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function onDelete(doc: Doc) {
    if (
      !window.confirm(
        `¿Borrar "${doc.filename}"? También se eliminarán sus chunks de la base de datos.`
      )
    )
      return;
    setDeletingId(doc.id);
    setError(null);
    try {
      await deleteDocument(doc.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <aside className="flex h-full w-72 flex-col border-r border-slate-200 bg-white">
      <div className="flex items-center gap-3 border-b border-slate-200 px-5 py-4">
        <span className="text-2xl">🎓</span>
        <div>
          <h1 className="text-sm font-semibold">RAG University</h1>
          <p className="text-xs text-slate-500">chat con tus documentos</p>
        </div>
      </div>

      <div className="p-4">
        <label className="flex cursor-pointer flex-col items-center gap-1.5 rounded-xl border-2 border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-500 transition hover:border-blue-400 hover:text-blue-600">
          <span className="text-2xl">⬆️</span>
          <span>{busy ? "Subiendo…" : "Subir PDF, TXT, MD, VTT o IPYNB"}</span>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.txt,.md,.vtt,.ipynb"
            className="hidden"
            onChange={onUpload}
            disabled={busy}
          />
        </label>
        {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
      </div>

      <div className="flex items-center justify-between px-4 pb-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Documentos
        </h2>
        <button onClick={refresh} className="text-xs text-blue-600 hover:underline">
          actualizar
        </button>
      </div>

      <ul className="chat-scroll flex-1 space-y-1 overflow-y-auto px-2 pb-4">
        {docs.map((d) => (
          <li
            key={d.id}
            className="group flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm hover:bg-slate-50"
          >
            <span className="truncate" title={d.filename}>
              {d.filename}
            </span>
            <span className="flex shrink-0 items-center gap-1.5">
              <span
                className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS[d.status].classes} ${
                  STATUS[d.status].pulse ? "animate-pulse" : ""
                }`}
              >
                {STATUS[d.status].label}
              </span>
              {deletingId === d.id ? (
                <span
                  className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-rose-500"
                  title="Borrando…"
                />
              ) : (
                <button
                  onClick={() => onDelete(d)}
                  disabled={deletingId !== null}
                  title="Borrar documento"
                  className="hidden text-slate-400 transition hover:text-rose-600 group-hover:inline disabled:opacity-40"
                >
                  🗑
                </button>
              )}
            </span>
          </li>
        ))}
        {docs.length === 0 && (
          <li className="px-3 py-6 text-center text-xs text-slate-400">
            Aún no hay documentos
          </li>
        )}
      </ul>

      <p className="border-t border-slate-200 px-4 py-3 text-[11px] text-slate-400">
        errores de ingesta → <code>.local-test/chunker.log</code>
      </p>
    </aside>
  );
}
