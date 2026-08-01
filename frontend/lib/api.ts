// Cliente de la API orquestadora. Las peticiones van a /api/*, que el
// servidor de Next.js proxifica a la API FastAPI (ver next.config.ts).

export type Doc = {
  id: number;
  filename: string;
  gcs_uri: string;
  status: "pending" | "ready" | "error";
  created_at: string;
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

export async function listDocuments(): Promise<Doc[]> {
  const res = await fetch(`${BASE}/documents`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Error ${res.status} listando documentos`);
  return res.json();
}

export async function uploadDocument(file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/documents`, { method: "POST", body: form });
  if (!res.ok) throw new Error(await res.text());
}

export async function ask(question: string, documentId?: number): Promise<QueryResponse> {
  const res = await fetch(`${BASE}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, document_id: documentId ?? null }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
