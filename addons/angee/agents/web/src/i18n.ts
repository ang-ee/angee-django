// English message bundle for the `agents` namespace. Components resolve these
// through `useAgentsT()` (below); the addon manifest contributes the bundle under
// `i18n.agents`. Keys are dotted by page. Metadata-driven field/column labels
// live in the SDL, not here — only bespoke component copy is routed.

import { createNamespaceT } from "@angee/ui";

export const enAgentsMessages: Record<string, string> = {
  // AgentsPage — bespoke form-section labels and record tabs.
  "agent.modelTemplates": "Model & operator templates",
  "agent.provisioningInputs": "Provisioning inputs",
  "agent.tabProvision": "Provision",
  "agent.tabService": "Service",
  "agent.tabWorkspace": "Workspace",
  "agent.tabChat": "Chat",
  "agent.noRunningAgent": "No agent yet",
  "agent.chatUnavailable":
    "The agent isn't running yet — provision it to start chatting.",
  "agent.setupAssistant": "Set up your assistant",

  // AgentChat — the live ACP chat surface (header, composer, settings cog).
  "chat.title": "Agent",
  "chat.resolving": "Connecting to your agent…",
  "chat.empty": "Ask the agent about what you're looking at — it has the notes tools.",
  "chat.placeholder": "Message the agent…",
  "chat.send": "Send",
  "chat.stop": "Stop",
  "chat.copy": "Copy",
  "chat.scrollToBottom": "Scroll to latest",
  "chat.commands": "Slash commands",
  "chat.commandsEmpty": "No matching commands",
  "chat.attach": "Attach image",
  "chat.removeAttachment": "Remove attachment",
  "chat.viewAttachment": "Current view",
  "chat.attachView": "Attach current view",
  "chat.inspectContext": "View context",
  "chat.clear": "Clear",
  "chat.reconnect": "Reconnect",
  "chat.settings": "Session settings",
  // Dense top bar: the agent (session/thread) chooser + the ⋯ overflow menu.
  "chat.switchAgent": "Switch agent",
  "chat.conversationOptions": "Conversation options",
  "chat.running": "Running",
  "chat.model": "Model",
  "chat.viewLabel": "View",
  "chat.mcpServers": "MCP servers",
  "chat.context": "System context",
  "chat.status.idle": "Idle",
  "chat.status.connecting": "Connecting…",
  "chat.status.ready": "Ready",
  "chat.status.error": "Error",
  "chat.status.closed": "Disconnected",

  // AgentSessionsPage — the full-page sessions view (left rail + conversation).
  "sessions.title": "Sessions",
  "sessions.railLabel": "Running agents",
  "sessions.new": "New agent",
  "sessions.running": "Running",

  // McpPage — bespoke form-section labels.
  "mcp.endpoint": "Endpoint",

  // InferencePage — actions and bespoke form-section labels.
  "inference.refreshModels": "Refresh models",
  "inference.backend": "Backend",
  "inference.catalogue": "Catalogue",
  "inference.provider": "Provider",
  "inference.capability": "Capability",
  "inference.status": "Status",
  "inference.credential": "Credential",
  "inference.connect.action": "Connect",
  "inference.connect.startError": "Could not start provider connection.",
  "inference.connect.connected": "Provider connected.",
  "inference.connect.openAuthorize": "Open the authorization page",
  "inference.connect.instructions": ", approve, then paste the code it shows below.",
  "inference.connect.codeLabel": "Authorization code",
  "inference.connect.codePlaceholder": "code#state",
  "inference.connect.codeIncomplete": "Paste the authorization code and state as code#state.",
  "inference.connect.codeMismatch": "The pasted state does not match this connection attempt.",
  "inference.connect.stateIncomplete": "The connection state is incomplete. Start again.",

  // AgentProvisioning — the embedded provisioning panel.
  "provisioning.title": "Provisioning",
  "provisioning.loading": "Loading…",
  "provisioning.saveFirst": "Save the agent to provision it.",
  "provisioning.intro":
    "Render this agent into an operator workspace and service from its templates.",
  "provisioning.provision": "Provision",
  "provisioning.deprovision": "Deprovision",
  "provisioning.needsTemplate": "Set a workspace template on this agent first.",
  "provisioning.workspace": "Workspace",
  "provisioning.service": "Service",
  "provisioning.workspacePanel": "Workspace",
  "provisioning.servicePanel": "Service",
  "provisioning.workspaceActions": "Workspace actions",
  "provisioning.serviceActions": "Service actions",
  "provisioning.activity": "Live operator activity",
  "provisioning.activityWaiting": "Waiting for the operator to create a workspace.",
  "provisioning.activityWaitingService": "Waiting for the operator to create a service.",
  "provisioning.activityConnected": "Streaming updates from the operator.",
  "provisioning.activityWorkspace": "Workspace",
  "provisioning.activityService": "Service",
  "provisioning.activityJobs": "Jobs",
  "provisioning.activityLogs": "Logs",
  "provisioning.workspaceExists": "Exists",
  "provisioning.workspaceTemplate": "Template",
  "provisioning.workspaceSources": "Sources",
  "provisioning.workspaceSourcesEmpty": "No workspace sources reported yet.",
  "provisioning.workspaceLogs": "Workspace logs",
  "provisioning.serviceRuntime": "Runtime",
  "provisioning.serviceHealth": "Health",
  "provisioning.serviceStatus": "Status",
  "provisioning.serviceLogs": "Service logs",
  "provisioning.sourceSlot": "Slot",
  "provisioning.sourceState": "State",
  "provisioning.sourceBranch": "Branch",
  "provisioning.sourceDrift": "Drift",
  "provisioning.sourcePath": "Path",
  "provisioning.clean": "Clean",
  "provisioning.dirty": "Dirty",
  "provisioning.yes": "Yes",
  "provisioning.no": "No",
  "provisioning.pending": "Pending",
  "provisioning.none": "None",
  "provisioning.live": "Live",
  "provisioning.logs": "Operator logs",
  "provisioning.actionFailed": "The action failed.",
  "provisioning.provisionFailed": "Provisioning failed.",
  "provisioning.confirmTitle": "Deprovision agent?",
  "provisioning.confirmBody":
    "The operator workspace and its services will be destroyed. This cannot be undone.",

  // SourcesPage — skill-source form.
  "sources.pointer": "Pointer",
  "sources.refreshSkills": "Refresh skills",
};

// A translator bound to the `agents` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useAgentsT = createNamespaceT("agents", enAgentsMessages);
