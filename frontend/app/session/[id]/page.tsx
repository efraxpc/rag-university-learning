"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import Chat from "../../../components/Chat";
import Sidebar from "../../../components/Sidebar";

// Página de una sesión (notebook): layout chat + sidebar filtrados por sesión.
export default function SessionPage() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const params = useParams();
  const sessionId = Number(params.id);

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900">
      {/* overlay móvil */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/30 md:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <div
        className={`fixed z-30 h-full transform transition-transform duration-200 md:static md:translate-x-0 ${
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <Sidebar sessionId={sessionId} />
      </div>
      <main className="flex min-w-0 flex-1 flex-col">
        <Chat
          sessionId={sessionId}
          onToggleSidebar={() => setSidebarOpen((v) => !v)}
        />
      </main>
    </div>
  );
}
