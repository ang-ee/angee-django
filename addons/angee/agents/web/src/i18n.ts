// English message bundle for the `agents` namespace. Components resolve these
// through `useAgentsT()` (below); the addon manifest contributes the bundle under
// `i18n.agents`. Keys are dotted by page. Metadata-driven field/column labels
// live in the SDL, not here — only bespoke component copy is routed.

import { createNamespaceT } from "@angee/ui";

export const enAgentsMessages: Record<string, string> = {
  // AgentsPage — bespoke form-section labels and record tabs.
  "agent.modelTemplates": "Model & operator templates",
  "agent.provisioningInputs": "Provisioning inputs",
  "agent.tabService": "Service",
  "agent.tabWorkspace": "Workspace",
  "agent.tabChat": "Chat",
  "agent.noRunningAgent": "No agent yet",
  "agent.chatUnavailable":
    "The agent isn't running yet — provision it to start chatting.",
  "agent.setupAssistant": "Set up your assistant",

  // AgentChat — the live ACP chat surface (header, composer, settings cog).
  "chat.title": "Agent",
  "chat.unavailable": "Chat is unavailable for this agent.",
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
  "chat.connectFailed": "Failed to connect to the agent.",
  "chat.responseFailed": "The agent did not respond.",

  // AgentSessionsPage — the full-page sessions view (left rail + conversation).
  "sessions.railLabel": "Running agents",
  "sessions.new": "New agent",
  "sessions.running": "Running",

  // Collection facets shared by the agents catalogue surfaces.
  "facet.vendor": "Vendor",
  "facet.source": "Source",
  "facet.repository": "Repository",
  "facet.server": "Server",

  // McpPage — bespoke form-section labels.
  "mcp.endpoint": "Endpoint",

  // InferencePage — actions and bespoke form-section labels.
  "inference.refreshModels": "Refresh models",
  "inference.backend": "Backend",
  "inference.catalogue": "Catalogue",
  "inference.provider": "Provider",
  "inference.credential": "Credential",
  "inference.connect.action": "Connect",
  "inference.connect.startError": "Could not start provider connection.",
  "inference.connect.connected": "Provider connected.",

  // AgentProvisioning — the embedded provisioning panel.
  "provisioning.loading": "Loading…",
  "provisioning.saveFirst": "Save the agent to provision it.",
  "provisioning.intro":
    "Render this agent into an operator workspace and service from its templates.",
  "provisioning.provision": "Provision",
  "provisioning.deprovision": "Deprovision",
  "provisioning.reprovision": "Reprovision",
  "provisioning.needsTemplate": "Set a workspace template on this agent first.",
  "provisioning.activityWaiting": "Waiting for the operator to create a workspace.",
  "provisioning.activityWaitingService": "Waiting for the operator to create a service.",
  "provisioning.workspaceSources": "Sources",
  "provisioning.workspaceSourcesEmpty": "No workspace sources reported yet.",
  "provisioning.serviceLogs": "Service logs",
  "provisioning.none": "None",
  "provisioning.deprovisionTitle": "Deprovision agent?",
  "provisioning.deprovisionBody":
    "The operator workspace and its services will be destroyed. This cannot be undone.",
  "provisioning.deprovisionBody.workspace":
    "The operator workspace “{name}” and the service mounting it are destroyed if they check out as this agent's; otherwise they are left in place and only this agent's record of them is cleared. This cannot be undone.",
  "provisioning.deprovisionBody.service":
    "This agent's workspace is destroyed, and the operator service “{name}” with it if the service mounts that workspace; otherwise the service is left in place. This cannot be undone.",
  "provisioning.reprovisionTitle": "Rebuild the agent's service?",
  "provisioning.reprovisionBody":
    "The service is destroyed and rendered again from this agent's current settings and credentials. The workspace and its files are kept.",
  "provisioning.adopt": "Adopt existing",
  "provisioning.adoptTitle.workspace": "Adopt the existing workspace?",
  "provisioning.adoptTitle.service": "Adopt the existing service?",
  "provisioning.adoptBody.workspace":
    "This agent takes over the operator workspace “{name}” and the service mounting it. The container is kept as it is, with the configuration and credentials it was created with, and started only if stopped. Reprovision afterwards to rebuild the service from this agent's current settings. The operator does not report which agent created a service, so the service is taken over because it mounts this workspace.",
  "provisioning.adoptBody.service":
    "This agent takes over the operator service “{name}”, which mounts this agent's workspace. The container is kept as it is, with the configuration and credentials it was created with, and started only if stopped. Reprovision afterwards to rebuild it from this agent's current settings. The operator does not report which agent created a service, so it is taken over because it mounts this workspace.",
  "provisioning.replace": "Replace existing",
  "provisioning.replaceTitle.workspace": "Replace the existing workspace?",
  "provisioning.replaceTitle.service": "Replace the existing service?",
  "provisioning.replaceBody.workspace":
    "The operator workspace “{name}”, its files and the service mounting it are destroyed, then this agent is provisioned afresh. This cannot be undone.",
  "provisioning.replaceBody.service":
    "The operator service “{name}” and this agent's workspace are destroyed, then this agent is provisioned afresh. This cannot be undone.",
  "provisioning.conflict.workspace":
    "The operator already has a workspace named “{name}” that this agent does not record: adopt it, replace it, or deprovision to clear the record.",
  "provisioning.conflict.service":
    "The operator already has a service named “{name}” that this agent does not record: adopt it, replace it, or deprovision to clear the record.",
  "provisioning.conflictSources": "Sources of the existing workspace",
  "provisioning.conflictLogs": "Logs of the existing service",

  // SourcesPage — skill-source form.
  "sources.pointer": "Pointer",
  "sources.refreshSkills": "Refresh skills",
};

// A translator bound to the `agents` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useAgentsT = createNamespaceT("agents", enAgentsMessages);
