import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// En desarrollo Vite hace de proxy de /api y /mcp hacia el backend, igual que
// nginx en producción: el frontend siempre llama al mismo origen.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "../../", "");
  const backend = env.VITE_BACKEND_PROXY || `http://localhost:${env.BACKEND_PORT || 8000}`;

  return {
    plugins: [react()],
    server: {
      port: parseInt(env.VITE_DEV_PORT) || 5173,
      host: "0.0.0.0",
      // Vite rechaza (403) cualquier Host que no sea localhost; en el compose
      // de dev se accede por IP/dominio de la VM.
      allowedHosts: env.VITE_ALLOW_ANY_HOST === "true" ? true : undefined,
      proxy: {
        // ws: la terminal SSH (/api/vms/{id}/terminal) es un WebSocket.
        "/api": { target: backend, ws: true },
        "/mcp": backend,
      },
    },
  };
});
