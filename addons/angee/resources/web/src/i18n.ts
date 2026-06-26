import { useNamespaceT } from "@angee/ui";
import type { MessageVars } from "@angee/refine";

export const enResourcesMessages: Record<string, string> = {
  "resources.col.hash": "Hash",
  "resources.col.loaded": "Loaded",
  "resources.col.source": "Source",
  "resources.col.sourceAddon": "Source addon",
  "resources.col.sourcePath": "Source file",
  "resources.col.target": "Target",
  "resources.col.tier": "Tier",
  "resources.empty.ledger": "No imported resources yet.",
};

export function useResourcesT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("resources", enResourcesMessages);
}
