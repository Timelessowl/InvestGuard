import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const proxy = {
    "/api": { target: env.API_PROXY_TARGET || "http://127.0.0.1:18473", changeOrigin: true },
    // Umami tracker for local runs; in Docker nginx does the same.
    ...(env.UMAMI_PROXY_TARGET && {
      "/stats": { target: env.UMAMI_PROXY_TARGET, changeOrigin: true, rewrite: (path: string) => path.replace(/^\/stats/, "") },
    }),
  };
  return {
    plugins: [react()],
    server: { port: 5173, strictPort: true, proxy },
    preview: { port: 4173, strictPort: true, proxy },
  };
});
