import { defineAngeeWebVitestConfig } from "@angee/app/vitest";

export default defineAngeeWebVitestConfig({
  // This starter has no generated-schema imports. Supply the host's runtime/gql
  // alias with gqlAliasFor when tests start consuming @angee/gql documents.
  gqlAlias: [],
});
