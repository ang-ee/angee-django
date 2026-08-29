import { useNamespaceT } from "../runtime";
import type { MessageVars } from "@angee/refine";

import { enUiMessages } from "./en";

export { enUiBundle, enUiMessages } from "./en";

export type UiMessageVars = MessageVars;

// The translate-function shape every ui/addon `useXT()` returns; helpers that
// take a translator parameter use this type instead of respelling the signature.
export type UiTranslate = (key: string, vars?: UiMessageVars) => string;

// A translator bound to the `ui` namespace: resolves against the host runtime's
// merged i18n first, then falls back to the bundled English. Thin alias over the
// shared `useNamespaceT` owner (the same pattern every addon's `useXT` uses).
export function useUiT(): (
  key: string,
  vars?: UiMessageVars,
) => string {
  return useNamespaceT("ui", enUiMessages);
}

export function createNamespaceT(
  namespace: string,
  messages: Record<string, string>,
): () => (key: string, vars?: MessageVars) => string {
  return function useCreatedNamespaceT() {
    return useNamespaceT(namespace, messages);
  };
}
