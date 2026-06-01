import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const django = process.env.ANGEE_DJANGO_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    proxy: {
      "/graphql/": { target: django, changeOrigin: false, ws: true },
      "/auth/csrf/": { target: django, changeOrigin: false },
    },
  },
});
