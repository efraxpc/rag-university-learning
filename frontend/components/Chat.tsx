"use client";

import { useState } from "react";
import { ask, type QueryResponse } from "../lib/api";

export default function Chat() {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onAsk() {
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await ask(question));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error desconocido");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="card">
      <h2>Pregunta a tus documentos</h2>
      <div className="row">
        <input
          type="text"
          placeholder="¿De qué trata el informe…?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onAsk()}
        />
        <button onClick={onAsk} disabled={loading || !question.trim()}>
          {loading ? "Pensando…" : "Preguntar"}
        </button>
      </div>

      {error && <p className="status-error">{error}</p>}

      {result && (
        <>
          <div className="answer">{result.answer}</div>
          {result.sources.length > 0 && (
            <div className="sources">
              <strong>Fuentes ({result.sources.length}):</strong>
              {result.sources.map((s, i) => (
                <details key={i}>
                  <summary>
                    {s.filename} (chunk · distancia {s.distance.toFixed(3)})
                  </summary>
                  <p>{s.content}</p>
                </details>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
