// English message bundle for the `agents` namespace. Components resolve these
// through `useAgentsT()` (below); the addon manifest contributes the bundle under
// `i18n.agents`. Keys are dotted by page. Metadata-driven field/column labels
// live in the SDL, not here — only bespoke component copy is routed.

import { useNamespaceT, type MessageVars } from "@angee/sdk";

export const enAgentsMessages: Record<string, string> = {
  // AgentsPage — bespoke form-section labels.
  "agents.agent.modelTemplates": "Model & operator templates",

  // McpPage — bespoke form-section labels.
  "agents.mcp.endpoint": "Endpoint",

  // InferencePage — actions and bespoke form-section labels.
  "agents.inference.refreshModels": "Refresh models",
  "agents.inference.backend": "Backend",
  "agents.inference.catalogue": "Catalogue",
};

// A translator bound to the `agents` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useAgentsT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("agents", enAgentsMessages);
}
