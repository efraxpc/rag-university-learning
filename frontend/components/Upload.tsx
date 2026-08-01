"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { listDocuments, uploadDocument, type Doc } from "../lib/api";

export default function Upload() {
  const fileRef = useRef<HTMLInputElement>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
    } catch {
      // API aún no disponible; se reintenta al pulsar "Actualizar".
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function onUpload() {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await uploadDocument(file);
      if (fileRef.current) fileRef.current.value = "";
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error desconocido");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Documentos</h2>
      <input ref={fileRef} type="file" accept=".pdf,.txt,.md,.ipynb" />
      <div className="row">
        <button onClick={onUpload} disabled={busy}>
          {busy ? "Subiendo…" : "Subir"}
        </button>
        <button onClick={refresh}>Actualizar</button>
      </div>
      {error && <p className="status-error">{error}</p>}
      <ul className="docs-list">
        {docs.map((d) => (
          <li key={d.id}>
            <span>{d.filename}</span>
            <span className={`status-${d.status}`}>{d.status}</span>
          </li>
        ))}
      </ul>
      {docs.length === 0 && (
        <p className="muted">Aún no hay documentos. Sube un PDF, TXT o MD.</p>
      )}
    </section>
  );
}
