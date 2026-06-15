// English message bundle for the `agents` namespace. Components resolve these
// through `useAgentsT()` (below); the addon manifest contributes the bundle under
// `i18n.agents`. Keys are dotted by page. Metadata-driven field/column labels
// live in the SDL, not here — only bespoke component copy is routed.

import { useNamespaceT, type MessageVars } from "@angee/sdk";

export const enAgentsMessages: Record<string, string> = {
  // AgentsPage — bespoke form-section labels and record tabs.
  "agents.agent.modelTemplates": "Model & operator templates",
  "agents.agent.provisioningInputs": "Provisioning inputs",
  "agents.agent.tabProvision": "Provision",
  "agents.agent.tabChat": "Chat",
  "agents.agent.chatUnavailable":
    "The agent isn't running yet — provision it to start chatting.",

  // McpPage — bespoke form-section labels.
  "agents.mcp.endpoint": "Endpoint",

  // InferencePage — actions and bespoke form-section labels.
  "agents.inference.refreshModels": "Refresh models",
  "agents.inference.backend": "Backend",
  "agents.inference.catalogue": "Catalogue",

  // AgentProvisioning — the embedded provisioning panel.
  "agents.provisioning.title": "Provisioning",
  "agents.provisioning.loading": "Loading…",
  "agents.provisioning.saveFirst": "Save the agent to provision it.",
  "agents.provisioning.intro":
    "Render this agent into an operator workspace and service from its templates.",
  "agents.provisioning.provision": "Provision",
  "agents.provisioning.deprovision": "Deprovision",
  "agents.provisioning.needsTemplate": "Set a workspace template on this agent first.",
  "agents.provisioning.workspace": "Workspace",
  "agents.provisioning.service": "Service",
  "agents.provisioning.actionFailed": "The action failed.",
  "agents.provisioning.provisionFailed": "Provisioning failed.",
  "agents.provisioning.deprovisionFailed": "Deprovisioning failed.",
  "agents.provisioning.confirmTitle": "Deprovision agent?",
  "agents.provisioning.confirmBody":
    "The operator workspace and its services will be destroyed. This cannot be undone.",

  // SourcesPage — skill-source form.
  "agents.sources.pointer": "Pointer",
  "agents.sources.refreshSkills": "Refresh skills",
};

// A translator bound to the `agents` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useAgentsT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("agents", enAgentsMessages);
}
