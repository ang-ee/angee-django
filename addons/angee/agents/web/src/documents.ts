// Non-CRUD console operations the agents pages invoke. Model CRUD is derived from
// the SDL by the DataPage; only bespoke action mutations are authored here.

import type { ActionOutcome, ByIdVariables } from "@angee/sdk";
import * as v from "valibot";

export const REFRESH_PROVIDER_MODELS_MUTATION = `
  mutation RefreshProviderModels($id: ID!) {
    refreshProviderModels(id: $id) {
      ok
      message
    }
  }
`;

/** `{ ok, message }` action outcome — the shared SDK contract. */
export type ActionResultData = ActionOutcome;

export interface RefreshProviderModelsData {
  refreshProviderModels: ActionResultData;
}

// Re-discover a skill source's skills — the integrate `refreshSource` action,
// invoked from the agents Skills → Sources tab.
export const REFRESH_SOURCE_MUTATION = `
  mutation AgentsRefreshSource($id: ID!) {
    refreshSource(id: $id) {
      ok
      message
    }
  }
`;

export interface RefreshSourceData {
  refreshSource: ActionResultData;
}

// Provision an agent through Django's server-side operator bridge.
export const PROVISION_AGENT_MUTATION = `
  mutation ProvisionAgent($id: ID!) {
    provisionAgent(id: $id) {
      ok
      message
    }
  }
`;

export interface ProvisionAgentData {
  provisionAgent: ActionResultData;
}

// Tear down the agent's operator workspace and services.
export const DEPROVISION_AGENT_MUTATION = `
  mutation DeprovisionAgent($id: ID!) {
    deprovisionAgent(id: $id) {
      ok
      message
    }
  }
`;

export interface DeprovisionAgentData {
  deprovisionAgent: ActionResultData;
}

/** Single-id action variables — the shared SDK contract. */
export type IdVariables = ByIdVariables;

// The browser-reachable chat endpoint for a running agent: the routed WebSocket URL
// (no token), a per-actor route token to append as `?token=`, the selected model handle
// to apply via ACP `session/set_model`, and the agent's rendered MCP server map to
// advertise on the ACP session. A mutation, not a query: each call mints a fresh,
// short-lived route token server-side (a side effect).
export const AGENT_CHAT_ENDPOINT_MUTATION = `
  mutation AgentChatEndpoint($id: ID!) {
    agentChatEndpoint(id: $id) {
      url
      token
      expiresAt
      mcpServers
      modelHandle
    }
  }
`;

// One MCP server entry inside the endpoint's `mcpServers` map. The map crosses the
// GraphQL `JSON` scalar, so its shape is opaque on the wire and must be parsed (not
// asserted) at the network boundary — `AgentChatEndpointSchema` does that with valibot.
const McpServerConfigSchema = v.object({
  type: v.string(),
  url: v.string(),
  headers: v.optional(v.record(v.string(), v.string())),
});

export type McpServerConfig = v.InferOutput<typeof McpServerConfigSchema>;

// The endpoint payload, with the opaque `mcpServers` JSON map narrowed per entry. The
// `agentChatEndpoint` mutation field is non-null server-side, so the wrapping data is
// required; only `mcpServers` rides the JSON scalar and so is validated here.
export const AgentChatEndpointSchema = v.object({
  url: v.string(),
  token: v.string(),
  expiresAt: v.string(),
  mcpServers: v.record(v.string(), McpServerConfigSchema),
  modelHandle: v.string(),
});

export type AgentChatEndpoint = v.InferOutput<typeof AgentChatEndpointSchema>;

export interface AgentChatEndpointData {
  agentChatEndpoint: AgentChatEndpoint;
}

// Render the `<system_context>` block for the agent and the user's open view. Called
// each send and prefixed to the user's text, so the agent reads what the user sees.
export const RENDER_AGENT_PROMPT_MUTATION = `
  mutation RenderAgentPrompt($id: ID!, $view: JSON!) {
    renderAgentPrompt(id: $id, view: $view)
  }
`;

export interface RenderAgentPromptData {
  renderAgentPrompt: string;
}

// The view envelope the chat sends to `renderAgentPrompt`: what the user is looking at.
export interface AgentChatView extends Record<string, unknown> {
  kind: "record" | "list" | "dashboard";
  type: string;
  sqid?: string;
  sqids?: string[];
  params?: Record<string, unknown>;
}

export interface RenderAgentPromptVariables extends Record<string, unknown> {
  id: string;
  view: AgentChatView;
}

// Resolve which agent serves the user's current view (the side chatter). Returns the
// agent identity only — the client then mints its chat endpoint with `agentChatEndpoint`;
// `null` means the user has no running agent (the chatter shows a call-to-action).
export const RESOLVE_SESSION_FOR_VIEW_MUTATION = `
  mutation ResolveSessionForView($view: JSON!) {
    resolveSessionForView(view: $view) {
      agentId
      agentName
      status
      modelHandle
    }
  }
`;

export interface AgentSession {
  agentId: string;
  agentName: string;
  status: string;
  modelHandle: string;
}

export interface AgentSessionData {
  resolveSessionForView: AgentSession | null;
}

export interface ResolveSessionVariables extends Record<string, unknown> {
  view: AgentChatView;
}
