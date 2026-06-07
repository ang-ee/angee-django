import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["*.test.ts", "src/**/*.test.ts", "src/**/*.test.tsx"],
    server: {
      deps: { inline: ["@angee/logo-react"] },
    },
  },
});
