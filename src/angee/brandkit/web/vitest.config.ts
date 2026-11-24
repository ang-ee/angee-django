import { defineAngeeWebVitestConfig } from "@angee/app/vitest";

export default defineAngeeWebVitestConfig({
  // The fragment authors no GraphQL documents: its page and form are metadata-driven.
  gqlAlias: [],
});
