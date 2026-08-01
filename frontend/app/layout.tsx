import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAG University",
  description: "Búsqueda conversacional sobre tus documentos (RAG en GCP)",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es">
      <body>{children}</body>
    </html>
  );
}
