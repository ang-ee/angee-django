import { useNamespaceT } from "../runtime";
import type { MessageVars } from "@angee/refine";

import { enBaseMessages } from "./en";

export { enBaseBundle, enBaseMessages } from "./en";

export type BaseMessageVars = MessageVars;

// A translator bound to the `base` namespace: resolves against the host runtime's
// merged i18n first, then falls back to the bundled English. Thin alias over the
// shared `useNamespaceT` owner (the same pattern every addon's `useXT` uses).
export function useBaseT(): (
  key: string,
  vars?: BaseMessageVars,
) => string {
  return useNamespaceT("base", enBaseMessages);
}
