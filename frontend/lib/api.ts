// Cliente de la API orquestadora. Las peticiones van a /api/*, que el
// servidor de Next.js proxifica a la API FastAPI (ver next.config.ts).

export type Doc = {
  id: number;
  filename: string;
  gcs_uri: string;
  status: "pending" | "ready" | "error";
  title: string | null; // título auto-generado de la clase (null = pendiente)
  session_id: number | null; // sesión (notebook) a la que pertenece
  created_at: string;
};

export type Session = {
  id: number;
  name: string;
  created_at: string;
  doc_count: number;
};

export type Source = {
  document_id: number;
  filename: string;
  content: string;
  distance: number;
};

export type QueryResponse = {
  answer: string;
  sources: Source[];
};

const BASE = "/api";

export async function listSessions(): Promise<Session[]> {
  const res = await fetch(`${BASE}/sessions`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Error ${res.status} listando sesiones`);
  return res.json();
}

export async function createSession(name: string): Promise<Session> {
  const res = await fetch(`${BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function deleteSession(id: number): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${id}`, { method: "DELETE" });
  // El 400 de "última sesión" llega en el body: se muestra tal cual.
  if (!res.ok) throw new Error(await res.text());
}

export async function listDocuments(sessionId?: number): Promise<Doc[]> {
  const url =
    sessionId === undefined
      ? `${BASE}/documents`
      : `${BASE}/documents?session_id=${sessionId}`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Error ${res.status} listando documentos`);
  return res.json();
}

export async function uploadDocument(file: File, sessionId: number): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  form.append("session_id", String(sessionId));
  const res = await fetch(`${BASE}/documents`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
}

export async function deleteDocument(id: number): Promise<void> {
  const res = await fetch(`${BASE}/documents/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
}

export async function ask(
  question: string,
  documentId?: number,
  summarize = false,
  documentIds?: number[],
  sessionId?: number,
): Promise<QueryResponse> {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      question,
      document_id: documentId ?? null,
      document_ids: documentIds ?? null,
      summarize,
      session_id: sessionId ?? null,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
