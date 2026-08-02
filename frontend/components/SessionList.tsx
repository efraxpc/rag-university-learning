"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  listSessions,
  createSession,
  deleteSession,
  type Session,
} from "../lib/api";

// Lista de sesiones (notebooks): crear, entrar y borrar con confirmación
// inline en la propia fila (mismo patrón que el borrado de Sidebar).
export default function SessionList() {
  const router = useRouter();
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSessions(await listSessions());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
      setSessions([]);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onCreate(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    setError(null);
    try {
      const s = await createSession(trimmed);
      router.push(`/session/${s.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
      setCreating(false);
    }
  }

  async function confirmDelete(session: Session) {
    setDeletingId(session.id);
    setError(null);
    try {
      await deleteSession(session.id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error desconocido");
    } finally {
      setDeletingId(null);
      setConfirmId(null);
    }
  }

  return (
    <div>
      <form onSubmit={onCreate} className="mb-6 flex gap-2">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Nombre de la nueva sesión…"
          className="flex-1 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
        />
        <button
          type="submit"
          disabled={creating || !name.trim()}
          className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-blue-700 disabled:bg-slate-300"
        >
          {creating ? "Creando…" : "＋ Crear sesión"}
        </button>
      </form>

      {error && <p className="mb-4 text-sm text-rose-600">{error}</p>}

      {sessions === null ? (
        <p className="py-10 text-center text-sm text-slate-400">Cargando…</p>
      ) : sessions.length === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white px-6 py-14 text-center">
          <span className="text-4xl">📚</span>
          <p className="mt-3 text-sm font-medium text-slate-600">
            Aún no tienes sesiones
          </p>
          <p className="mt-1 text-xs text-slate-400">
            Crea una para empezar a subir documentos y preguntar sobre ellos.
          </p>
        </div>
      ) : (
        <ul className="space-y-2">
          {sessions.map((s) => (
            <li
              key={s.id}
              className="group flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 transition hover:border-blue-300 hover:shadow-sm"
            >
              <Link href={`/session/${s.id}`} className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-slate-800 group-hover:text-blue-600">
                  {s.name}
                </p>
                <p className="mt-0.5 text-xs text-slate-400">
                  {s.doc_count} documento{s.doc_count !== 1 ? "s" : ""} · creada
                  el{" "}
                  {new Date(s.created_at).toLocaleDateString("es-ES", {
                    day: "numeric",
                    month: "long",
                    year: "numeric",
                  })}
                </p>
              </Link>
              <span className="flex shrink-0 items-center gap-1.5">
                {confirmId === s.id && deletingId !== s.id ? (
                  // Confirmación inline en la misma fila (sin modal).
                  <>
                    <span className="text-[11px] font-medium text-rose-600">
                      ¿Borrar?
                    </span>
                    <button
                      onClick={() => confirmDelete(s)}
                      title="Confirmar borrado"
                      className="text-xs text-rose-500 transition hover:text-rose-700"
                    >
                      ✓
                    </button>
                    <button
                      onClick={() => setConfirmId(null)}
                      title="Cancelar"
                      className="text-xs text-slate-400 transition hover:text-slate-600"
                    >
                      ✕
                    </button>
                  </>
                ) : deletingId === s.id ? (
                  <span
                    className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-300 border-t-rose-500"
                    title="Borrando…"
                  />
                ) : (
                  <button
                    onClick={() => setConfirmId(s.id)}
                    disabled={deletingId !== null}
                    title="Borrar sesión"
                    className="hidden text-slate-400 transition hover:text-rose-600 group-hover:inline disabled:opacity-40"
                  >
                    🗑
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
