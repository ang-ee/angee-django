import { createNamespaceT } from "@angee/ui";

export const enWorkflowsPartiesMessages: Record<string, string> = {
  "dedupe.run": "Run dedupe",
  "dedupe.running": "Starting…",
  "dedupe.description": "Scan for duplicates and review the proposed merges as one batch.",
  "dedupe.failed": "Could not start the dedupe run.",
};

export const useWorkflowsPartiesT = createNamespaceT("workflows-parties", enWorkflowsPartiesMessages);
