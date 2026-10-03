import { defineAngeePackageVitestConfig } from "../vitest.shared";

export default defineAngeePackageVitestConfig({
  test: { setupFiles: ["../ui/src/test-setup.ts"] },
});
