import { defineAngeePackageVitestConfig } from "../vitest.shared";

export default defineAngeePackageVitestConfig({
  test: { setupFiles: ["./src/test-setup.ts"] },
});
