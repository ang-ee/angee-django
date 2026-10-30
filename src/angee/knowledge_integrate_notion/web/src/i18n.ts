import { createNamespaceT } from "@angee/ui";

export const enNotionMessages: Record<string, string> = {
  "action.sync": "Sync now",
  "group.sync": "Sync",
};

export const useNotionT = createNamespaceT("knowledge_integrate_notion", enNotionMessages);
