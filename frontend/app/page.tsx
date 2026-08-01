import Chat from "../components/Chat";
import Upload from "../components/Upload";

export default function Home() {
  return (
    <main className="container">
      <h1>RAG University</h1>
      <p className="subtitle">
        Pregunta a tus documentos (PDF/TXT/MD/IPYNB) con búsqueda semántica + Gemini.
      </p>
      <div className="grid">
        <Upload />
        <Chat />
      </div>
    </main>
  );
}
