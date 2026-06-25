import { schemaCodegen } from "./codegen.shared";

// The example uses the default monorepo document roots (or the
// `ANGEE_CODEGEN_DOCUMENT_ROOTS` env override). A downstream project that
// prefers checked-in roots passes its install-layout roots explicitly, e.g.
// `schemaCodegen("public", ["node_modules/@angee/*/web/src", "./src"])`.
export default schemaCodegen("public");
