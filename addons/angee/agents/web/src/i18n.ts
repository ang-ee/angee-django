// English message bundle for the `agents` namespace. Components resolve these
// through `useAgentsT()` (below); the addon manifest contributes the bundle under
// `i18n.agents`. Keys are dotted by page. Metadata-driven field/column labels
// live in the SDL, not here — only bespoke component copy is routed.

import { useNamespaceT } from "@angee/ui";
import type { MessageVars } from "@angee/refine";

export const enAgentsMessages: Record<string, string> = {
  // AgentsPage — bespoke form-section labels and record tabs.
  "agents.agent.modelTemplates": "Model & operator templates",
  "agents.agent.provisioningInputs": "Provisioning inputs",
  "agents.agent.tabProvision": "Provision",
  "agents.agent.tabService": "Service",
  "agents.agent.tabWorkspace": "Workspace",
  "agents.agent.tabChat": "Chat",
  "agents.agent.noRunningAgent": "No agent yet",
  "agents.agent.chatUnavailable":
    "The agent isn't running yet — provision it to start chatting.",
  "agents.agent.setupAssistant": "Set up your assistant",

  // AgentChat — the live ACP chat surface (header, composer, settings cog).
  "agents.chat.title": "Agent",
  "agents.chat.resolving": "Connecting to your agent…",
  "agents.chat.empty": "Ask the agent about what you're looking at — it has the notes tools.",
  "agents.chat.placeholder": "Message the agent…",
  "agents.chat.send": "Send",
  "agents.chat.clear": "Clear",
  "agents.chat.reconnect": "Reconnect",
  "agents.chat.settings": "Session settings",
  "agents.chat.model": "Model",
  "agents.chat.viewLabel": "View",
  "agents.chat.mcpServers": "MCP servers",
  "agents.chat.context": "System context",
  "agents.chat.status.idle": "Idle",
  "agents.chat.status.connecting": "Connecting…",
  "agents.chat.status.ready": "Ready",
  "agents.chat.status.error": "Error",
  "agents.chat.status.closed": "Disconnected",

  // McpPage — bespoke form-section labels.
  "agents.mcp.endpoint": "Endpoint",

  // InferencePage — actions and bespoke form-section labels.
  "agents.inference.refreshModels": "Refresh models",
  "agents.inference.backend": "Backend",
  "agents.inference.catalogue": "Catalogue",
  "agents.inference.provider": "Provider",
  "agents.inference.capability": "Capability",
  "agents.inference.status": "Status",
  "agents.inference.credential": "Credential",
  "agents.inference.connect.action": "Connect",
  "agents.inference.connect.startError": "Could not start provider connection.",
  "agents.inference.connect.connected": "Provider connected.",
  "agents.inference.connect.openAuthorize": "Open the authorization page",
  "agents.inference.connect.instructions": ", approve, then paste the code it shows below.",
  "agents.inference.connect.codeLabel": "Authorization code",
  "agents.inference.connect.codePlaceholder": "code#state",
  "agents.inference.connect.codeIncomplete": "Paste the authorization code and state as code#state.",
  "agents.inference.connect.codeMismatch": "The pasted state does not match this connection attempt.",
  "agents.inference.connect.stateIncomplete": "The connection state is incomplete. Start again.",

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
  "agents.provisioning.workspacePanel": "Workspace",
  "agents.provisioning.servicePanel": "Service",
  "agents.provisioning.workspaceActions": "Workspace actions",
  "agents.provisioning.serviceActions": "Service actions",
  "agents.provisioning.activity": "Live operator activity",
  "agents.provisioning.activityWaiting": "Waiting for the operator to create a workspace.",
  "agents.provisioning.activityWaitingService": "Waiting for the operator to create a service.",
  "agents.provisioning.activityConnected": "Streaming updates from the operator.",
  "agents.provisioning.activityWorkspace": "Workspace",
  "agents.provisioning.activityService": "Service",
  "agents.provisioning.activityJobs": "Jobs",
  "agents.provisioning.activityLogs": "Logs",
  "agents.provisioning.workspaceExists": "Exists",
  "agents.provisioning.workspaceTemplate": "Template",
  "agents.provisioning.workspaceSources": "Sources",
  "agents.provisioning.workspaceSourcesEmpty": "No workspace sources reported yet.",
  "agents.provisioning.workspaceLogs": "Workspace logs",
  "agents.provisioning.serviceRuntime": "Runtime",
  "agents.provisioning.serviceHealth": "Health",
  "agents.provisioning.serviceStatus": "Status",
  "agents.provisioning.serviceLogs": "Service logs",
  "agents.provisioning.sourceSlot": "Slot",
  "agents.provisioning.sourceState": "State",
  "agents.provisioning.sourceBranch": "Branch",
  "agents.provisioning.sourceDrift": "Drift",
  "agents.provisioning.sourcePath": "Path",
  "agents.provisioning.clean": "Clean",
  "agents.provisioning.dirty": "Dirty",
  "agents.provisioning.yes": "Yes",
  "agents.provisioning.no": "No",
  "agents.provisioning.pending": "Pending",
  "agents.provisioning.none": "None",
  "agents.provisioning.live": "Live",
  "agents.provisioning.logs": "Operator logs",
  "agents.provisioning.actionFailed": "The action failed.",
  "agents.provisioning.provisionFailed": "Provisioning failed.",
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
