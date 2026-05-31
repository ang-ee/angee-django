import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // Pure modules run under node; React hook suites opt into a DOM
    // environment per-file with a `// @vitest-environment jsdom` pragma.
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
