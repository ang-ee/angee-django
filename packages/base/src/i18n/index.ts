import {
  translateWithFallback,
  useT,
  type MessageVars,
} from "@angee/sdk";

import { enBaseMessages } from "./en";

export { enBaseBundle, enBaseMessages } from "./en";

export type BaseMessageVars = MessageVars;

// A translator bound to the `base` namespace. Resolves against the host
// runtime's merged i18n first, then falls back to the bundled English strings.
export function useBaseT(): (
  key: string,
  vars?: BaseMessageVars,
) => string {
  const t = useT("base");
  return (key: string, vars?: BaseMessageVars) =>
    translateWithFallback(t, enBaseMessages, key, vars);
}
