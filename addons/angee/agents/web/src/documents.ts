// Non-CRUD console operations the agents pages invoke. Model CRUD is derived from
// the SDL by the ResourceList; only bespoke *custom* operations are authored here as
// typed `graphql()` documents (no hand-written result types). Single-id
// `{ ok, message }` action mutations use `useActionMutation(field)` from
// `@angee/refine` at the call site — no document is authored here.

import { graphql, type DocumentType } from "@angee/gql/console";
import * as v from "valibot";

// The browser-reachable chat endpoint for a running agent: the routed WebSocket URL
// (no token), a per-actor route token to append as `?token=`, the selected model handle
// to apply via ACP `session/set_model`, and the agent's rendered MCP server map to
// advertise on the ACP session. A mutation, not a query: each call mints a fresh,
// short-lived route token server-side (a side effect).
export const AgentChatEndpointMutation = graphql(`
  mutation AgentChatEndpoint($id: ID!) {
    agent_chat_endpoint(id: $id) {
      url
      token
      expires_at
      mcp_servers
      model_handle
    }
  }
`);

// One MCP server entry inside the endpoint's `mcp_servers` map. The map crosses the
// GraphQL `JSON` scalar, so its shape is opaque on the wire and must be parsed (not
// asserted) at the network boundary — `AgentChatEndpointSchema` does that with valibot.
const McpServerConfigSchema = v.object({
  type: v.string(),
  url: v.string(),
  headers: v.optional(v.record(v.string(), v.string())),
});

export type McpServerConfig = v.InferOutput<typeof McpServerConfigSchema>;

// The endpoint payload, with the opaque `mcp_servers` JSON map narrowed per entry. The
// `agent_chat_endpoint` mutation field is non-null server-side, so the wrapping data is
// required; only `mcp_servers` rides the JSON scalar and so is validated here.
export const AgentChatEndpointSchema = v.object({
  url: v.string(),
  token: v.string(),
  expires_at: v.string(),
  mcp_servers: v.record(v.string(), McpServerConfigSchema),
  model_handle: v.string(),
});

export type AgentChatEndpoint = v.InferOutput<typeof AgentChatEndpointSchema>;

// Render the `<system_context>` block for the agent and the user's open view. Rendered
// fresh each send and emitted by `buildPromptBlocks` as its OWN leading `ContentBlock`
// (an embedded `resource` when the agent advertises `embeddedContext`, else a plain
// `text` block) — never prefixed or string-merged into the user's text.
export const RenderAgentPrompt = graphql(`
  mutation RenderAgentPrompt($id: ID!, $view: JSON!) {
    render_agent_prompt(id: $id, view: $view)
  }
`);

export const ConnectInferenceProvider = graphql(`
  mutation ConnectInferenceProvider(
    $id: ID!
    $redirectUri: String!
    $next: String!
  ) {
    connect_inference_provider(
      id: $id
      redirect_uri: $redirectUri
      next: $next
    ) {
      ...ConnectOAuthResultFields
    }
  }
`);

export const UpdateInferenceProvider = graphql(`
  mutation UpdateInferenceProvider($data: InferenceProviderPatch!) {
    update_inference_provider(data: $data) {
      id
      display_name
      name
      owner {
        id
      }
      backend_class
      vendor {
        id
      }
      credential {
        id
        display_name
      }
      account {
        id
        display_name
      }
      status
      base_url
      config
      created_at
      updated_at
    }
  }
`);

// The view envelope the chat sends to `render_agent_prompt`: what the user is looking at.
export interface AgentChatView extends Record<string, unknown> {
  kind: "record" | "list" | "dashboard";
  type: string;
  sqid?: string;
  sqids?: string[];
  params?: Record<string, unknown>;
}

// Resolve which agent serves the user's current view (the side chatter). Returns the
// agent identity only — the client then mints its chat endpoint with `agent_chat_endpoint`;
// `null` means the user has no running agent (the chatter shows a call-to-action).
export const ResolveSessionForView = graphql(`
  query ResolveSessionForView($view: JSON!) {
    resolve_session_for_view(view: $view) {
      agent_id
      agent_name
      status
      model_handle
    }
  }
`);

/** The resolved running-agent identity for a view; `null` when none runs. */
export type AgentSession = NonNullable<
  DocumentType<typeof ResolveSessionForView>["resolve_session_for_view"]
>;

// The full agent roster the chat surfaces switch between: EVERY agent ordered by recent
// activity (templates and stopped/errored agents included), filtered client-side to the
// running ones by `useRunningAgents`. Each running agent owns its own durable ACP session,
// so the filtered result is a SESSION/THREAD list, not a model catalogue. `runtime_status`
// types as the `RuntimeStatus` enum union (UPPERCASE — RUNNING/STOPPED/…) and `model` is
// nullable on `AgentType`, so consumers read `agent.model?.name`.
export const AgentRoster = graphql(`
  query AgentRoster {
    agents(order_by: [{ updated_at: desc }]) {
      id
      name
      runtime_status
      is_template
      updated_at
      model {
        name
      }
    }
  }
`);

/** One roster row from `AgentRoster` (the chooser + sessions rail item, after filtering). */
export type AgentRosterItem = NonNullable<
  DocumentType<typeof AgentRoster>["agents"]
>[number];
