/**
 * Vite plugin that provides a no-op stub for @angee/gql/* when the full
 * stack runtime/gql directory is not present (standalone checkout).
 *
 * Without this, addons that import @angee/gql/console (proposals, workflows)
 * cause a pre-bundling hard error that prevents Storybook from starting at all.
 * The stub makes those imports resolve to empty exports so Storybook starts
 * and only the stories from those specific addons are non-functional.
 */
export function gqlStubPlugin() {
  const VIRTUAL_PREFIX = "\0gql-stub:";
  return {
    name: "angee-gql-stub",
    resolveId(id) {
      if (id === "@angee/gql" || id.startsWith("@angee/gql/")) {
        return VIRTUAL_PREFIX + id;
      }
    },
    load(id) {
      if (id.startsWith(VIRTUAL_PREFIX)) {
        // Return a stub that exports a no-op `graphql` tag and common GQL
        // utilities so imports don't crash at module evaluation time.
        return `
export function graphql(strings, ...values) {
  return strings.raw ? strings.raw.join('') : String(strings);
}
export const gql = graphql;
export default graphql;
`;
      }
    },
  };
}
