import type { NextConfig } from "next";

// Proxy server-side /api/* → API FastAPI interna. Así el navegador solo
// habla con este servicio y no hace falta Load Balancer ni exponer la API.
const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // En dev, Next limita las peticiones proxificadas (rewrites) a 30 s por
    // defecto y aborta el socket → "Failed to proxy ... ECONNRESET". Las
    // consultas RAG tardan ~30-60 s (query opt + embeddings + generación),
    // así que hay que dar margen. OJO: 0 NO lo desactiva (cae al default).
    proxyTimeout: 120_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_INTERNAL_URL}/:path*` }];
  },
};

export default nextConfig;
