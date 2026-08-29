import { createNamespaceT } from "@angee/ui";

export const enResourcesMessages: Record<string, string> = {
  "col.hash": "Hash",
  "col.loaded": "Loaded",
  "col.source": "Source",
  "col.sourceAddon": "Source addon",
  "col.sourcePath": "Source file",
  "col.target": "Target",
  "col.tier": "Tier",
  "empty.ledger": "No imported resources yet.",
};

export const useResourcesT = createNamespaceT("resources", enResourcesMessages);
