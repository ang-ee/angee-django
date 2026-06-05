import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const django = process.env.ANGEE_DJANGO_URL ?? "http://127.0.0.1:8000";
// The operator daemon (the dev-stack supervisor) the console talks to. The
// workspace allocates its port and the stack exports ANGEE_OPERATOR_URL.
const operator = process.env.ANGEE_OPERATOR_URL ?? "http://127.0.0.1:9000";
// The angee workspace allocates a unique UI port and exports it; honour it so a
// workspace's frontend (and the e2e harness targeting it) do not collide on 5173.
const uiPort = Number(process.env.ANGEE_UI_PORT ?? 5173);

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true,
    port: uiPort,
    strictPort: true,
    proxy: {
      "/graphql/": { target: django, changeOrigin: false, ws: true },
      "/auth/csrf/": { target: django, changeOrigin: false },
      // Proxy ONLY the daemon GraphQL endpoint (Django sets
      // ANGEE_OPERATOR_GRAPHQL_ENDPOINT=/operator/graphql), stripping the prefix so
      // it lands on the daemon's own /graphql — no cross-origin. Scoped to the exact
      // path so the SPA's own /operator/* page routes still hard-reload to index.html.
      "/operator/graphql": {
        target: operator,
        changeOrigin: false,
        ws: true,
        rewrite: (path) => path.replace(/^\/operator/, ""),
      },
    },
  },
});
