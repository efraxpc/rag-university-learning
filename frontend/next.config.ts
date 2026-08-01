import type { NextConfig } from "next";

// Proxy server-side /api/* → API FastAPI interna. Así el navegador solo
// habla con este servicio y no hace falta Load Balancer ni exponer la API.
const API_INTERNAL_URL = process.env.API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_INTERNAL_URL}/:path*` }];
  },
};

export default nextConfig;
