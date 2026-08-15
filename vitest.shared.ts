// Residual host wrapper only. The React subtree (the sibling angee-react
// checkout) and the base-addon subtree own their own shared Vitest
// configuration; the example host supplies its generated-schema alias to these
// project-neutral builders, reached by package name through the workspace.
export {
  defineAngeeWebVitestConfig,
  gqlAliasFor,
} from "@angee/app/vitest";
