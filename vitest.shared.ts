// Residual host wrapper only. The React and base-addon subtrees own their own
// shared Vitest configuration; the example host supplies its generated-schema
// alias to these project-neutral builders.
export {
  defineAngeeWebVitestConfig,
  gqlAliasFor,
} from "./angee/web/app/config/vitest";
