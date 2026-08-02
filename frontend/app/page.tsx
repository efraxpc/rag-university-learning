"use client";

import SessionList from "../components/SessionList";

// Home: listado de sesiones (notebooks) de estudio.
export default function Home() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-3xl px-4 py-10">
        <header className="mb-8 flex items-center gap-3">
          <span className="text-4xl">🎓</span>
          <div>
            <h1 className="text-xl font-semibold">RAG University</h1>
            <p className="text-sm text-slate-500">Tus sesiones de estudio</p>
          </div>
        </header>
        <SessionList />
      </div>
    </div>
  );
}
