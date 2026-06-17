import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Pure projectors run under node; component suites opt into a DOM environment
    // per-file with a `// @vitest-environment happy-dom` pragma.
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    server: {
      // The chrome barrel pulls in the logo's stylesheet; inline that dep so
      // Vite resolves its `.css` import instead of Node's ESM loader rejecting it.
      deps: { inline: ["@angee/logo-react"] },
    },
  },
});
