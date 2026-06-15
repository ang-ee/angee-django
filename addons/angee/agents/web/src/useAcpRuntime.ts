// The ACP chat runtime: opens a forward-authed WebSocket to a running agent, drives
// the ACP session (initialize → newSession → prompt/cancel), folds `session/update`
// chunks into an assistant-ui external store, and renders the result through
// `AssistantRuntimeProvider`. The browser speaks ACP to the agent through the
// operator's central Caddy; the route token is short-lived, so the endpoint is
// re-queried and the socket reconnected when the token nears expiry.

import * as React from "react";
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  ClientSideConnection,
  PROTOCOL_VERSION,
  type Agent,
  type Client,
  type McpServer,
  type RequestPermissionRequest,
  type RequestPermissionResponse,
  type SessionNotification,
  type ToolCallStatus,
} from "@zed-industries/agent-client-protocol";
import * as v from "valibot";
import { useAuthoredMutation } from "@angee/sdk";

import { openAcpTransport, type AcpTransport } from "./acp-transport";
import {
  AGENT_CHAT_ENDPOINT_MUTATION,
  AgentChatEndpointSchema,
  RENDER_AGENT_PROMPT_MUTATION,
  type AgentChatEndpoint,
  type AgentChatEndpointData,
  type AgentChatView,
  type IdVariables,
  type McpServerConfig,
  type RenderAgentPromptData,
  type RenderAgentPromptVariables,
} from "./documents";

// Re-mint the route token this far before it expires, so the socket reconnects while
// the old one is still valid rather than after the agent has dropped it.
const TOKEN_REFRESH_MARGIN_MS = 60_000;

/** The chat connection lifecycle, surfaced to the view's status header. */
export type AcpStatus = "idle" | "connecting" | "ready" | "error" | "closed";

/** A chat message held in the external store: role, text body, and tool-call blocks. */
interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  toolCalls: ToolCallBlock[];
}

interface ToolCallBlock {
  id: string;
  title: string;
  status: ToolCallStatus;
}

export interface AcpRuntime {
  runtime: ReturnType<typeof useExternalStoreRuntime>;
  status: AcpStatus;
  error: string | null;
}

/**
 * Build the assistant-ui runtime for chatting with the agent identified by `agentId`,
 * about the user's open `view`.
 *
 * Holds the message log as immutable React state: each `session/update` replaces the
 * in-flight assistant message with a fresh object, so assistant-ui's identity-keyed
 * message cache re-renders the streamed text.
 */
export function useAcpRuntime(agentId: string, view: AgentChatView): AcpRuntime {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [status, setStatus] = React.useState<AcpStatus>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [isRunning, setIsRunning] = React.useState(false);

  const connectionRef = React.useRef<Agent | null>(null);
  const transportRef = React.useRef<AcpTransport | null>(null);
  const sessionIdRef = React.useRef<string | null>(null);
  const viewRef = React.useRef(view);
  viewRef.current = view;

  const [mintEndpoint] = useAuthoredMutation<AgentChatEndpointData, IdVariables>(
    AGENT_CHAT_ENDPOINT_MUTATION,
  );
  const [renderPrompt] = useAuthoredMutation<RenderAgentPromptData, RenderAgentPromptVariables>(
    RENDER_AGENT_PROMPT_MUTATION,
  );

  // Fold one `session/update` into the log as a NEW object: assistant-ui caches converted
  // messages by source identity, so a fresh object per chunk is what makes streamed text
  // re-render (an in-place mutation keeps the identity and is dropped).
  const onUpdate = React.useCallback((note: SessionNotification): void => {
    setMessages((log) => foldIntoLog(log, note));
  }, []);
  // The connect effect builds the ACP client once; it reads `onUpdate` through a ref so a
  // callback-identity change never tears down and reconnects the socket (which tracks
  // `agentId` alone).
  const onUpdateRef = React.useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  // Connect on mount; reconnect before the route token expires; tear the socket down on
  // unmount or agent change. A per-effect `active` flag gates every state update and the
  // post-await continuations, so an in-flight connect for a stale agent never clobbers
  // the live one (it resolves after cleanup set `active = false`).
  React.useEffect(() => {
    let active = true;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    // A new agent starts from an empty transcript. The effect re-runs on `agentId`, so this
    // clears the previous agent's messages; an in-effect token-refresh reconnect does not
    // re-run it, so it keeps the live conversation.
    setMessages([]);
    setIsRunning(false);

    const tearDown = (): void => {
      transportRef.current?.close();
      transportRef.current = null;
      connectionRef.current = null;
      sessionIdRef.current = null;
    };

    const connect = async (silent = false): Promise<void> => {
      // A scheduled token re-mint reconnects silently — it keeps the status "ready" so the
      // composer isn't disabled mid-conversation; only a genuine failure surfaces.
      if (!silent) setStatus("connecting");
      setError(null);
      try {
        const endpoint = await mintEndpoint({ id: agentId });
        if (!active) return;
        const validated = parseEndpoint(endpoint);
        const transport = openAcpTransport(validated.url, validated.token);
        transportRef.current = transport;
        const connection = new ClientSideConnection(() => makeClient(onUpdateRef), transport.stream);
        connectionRef.current = connection;
        await transport.ready;
        if (!active) return;
        await connection.initialize({
          protocolVersion: PROTOCOL_VERSION,
          clientCapabilities: {},
        });
        if (!active) return;
        // Straight to a session: Claude Code authenticates from its env token
        // (CLAUDE_CODE_OAUTH_TOKEN, synced at provision), and its ACP `authenticate`
        // method is not implemented — it advertises `claude-login` only as a terminal
        // hint. A genuinely unauthenticated agent fails `newSession` here, which the
        // catch below surfaces, rather than hanging.
        const session = await connection.newSession({
          cwd: "/workspace",
          mcpServers: toMcpServers(validated.mcpServers),
        });
        if (!active) return;
        sessionIdRef.current = session.sessionId;
        setStatus("ready");
        scheduleRefresh(validated.expiresAt);
        // Only the *current* transport's close means the chat dropped — a scheduled
        // refresh closes the previous socket itself, and that close must not clobber the
        // freshly reconnected one.
        void transport.closed.then(() => {
          if (active && transportRef.current === transport) setStatus("closed");
        });
      } catch (caught) {
        if (!active) return;
        setStatus("error");
        setError(caught instanceof Error ? caught.message : "Failed to connect to the agent.");
      }
    };

    // Re-mint the token and reconnect a margin before it expires; an unparseable or past
    // `expiresAt` simply skips the refresh, leaving the connect-once socket in place.
    const scheduleRefresh = (expiresAt: string): void => {
      const delay = Date.parse(expiresAt) - Date.now() - TOKEN_REFRESH_MARGIN_MS;
      if (Number.isNaN(delay) || delay <= 0) return;
      refreshTimer = setTimeout(() => {
        tearDown();
        void connect(true);
      }, delay);
    };

    void connect();
    return () => {
      active = false;
      if (refreshTimer !== undefined) clearTimeout(refreshTimer);
      tearDown();
    };
  }, [agentId, mintEndpoint]);

  const onNew = React.useCallback(
    async (message: AppendMessage): Promise<void> => {
      const connection = connectionRef.current;
      const sessionId = sessionIdRef.current;
      const userText = textOf(message);
      if (connection === null || sessionId === null || userText === "") return;

      setMessages((log) => [
        ...log,
        { id: `user-${log.length}`, role: "user", text: userText, toolCalls: [] },
      ]);
      setIsRunning(true);
      try {
        const context = await renderContext(renderPrompt, agentId, viewRef.current);
        await connection.prompt({
          sessionId,
          prompt: [{ type: "text", text: context === "" ? userText : `${context}\n\n${userText}` }],
        });
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "The agent did not respond.");
      } finally {
        setIsRunning(false);
      }
    },
    [agentId, renderPrompt],
  );

  const onCancel = React.useCallback(async (): Promise<void> => {
    const connection = connectionRef.current;
    const sessionId = sessionIdRef.current;
    if (connection !== null && sessionId !== null) await connection.cancel({ sessionId });
    setIsRunning(false);
  }, []);

  const runtime = useExternalStoreRuntime({
    isRunning,
    messages,
    onNew,
    onCancel,
    convertMessage,
  });

  return { runtime, status, error };
}

/** Convert a stored chat message to the assistant-ui thread shape. */
function convertMessage(message: ChatMessage): ThreadMessageLike {
  // The ACP execution status rides in the tool-call part's `args` (its typed JSON input
  // bag) — `result` is reserved for the tool's actual output — and `ToolBlock` renders it.
  const toolParts = message.toolCalls.map((call) => ({
    type: "tool-call" as const,
    toolCallId: call.id,
    toolName: call.title,
    args: { status: call.status },
    argsText: "",
  }));
  const textParts = message.text === "" ? [] : [{ type: "text" as const, text: message.text }];
  return { id: message.id, role: message.role, content: [...textParts, ...toolParts] };
}

/** Build the ACP `Client` handler: stream updates, auto-approve permission prompts. */
function makeClient(
  onUpdateRef: React.MutableRefObject<(note: SessionNotification) => void>,
): Client {
  return {
    async sessionUpdate(note: SessionNotification): Promise<void> {
      onUpdateRef.current(note);
    },
    async requestPermission(
      params: RequestPermissionRequest,
    ): Promise<RequestPermissionResponse> {
      // Auto-approve every requested permission. The agent works inside its own provisioned
      // container/workspace, and its notes MCP tools are authorized server-side by rebac (the
      // agent actor's grants), so client approval is a UX confirmation here, not the security
      // boundary — auto-approving does not widen what the agent may touch. Match the exact
      // allow kinds, never an `allow*` prefix, so a future kind is not silently approved; a
      // richer in-thread prompt UI is future work.
      const allow = params.options.find(
        (option) => option.kind === "allow_once" || option.kind === "allow_always",
      );
      if (allow === undefined) return { outcome: { outcome: "cancelled" } };
      return { outcome: { outcome: "selected", optionId: allow.optionId } };
    },
  };
}

/** Validate the minted endpoint payload at the network boundary (its `mcpServers` map
 * rides the GraphQL `JSON` scalar, so its shape is opaque on the wire and must be parsed,
 * not asserted). Throws on a missing or malformed payload — the caller shows the error. */
function parseEndpoint(data: AgentChatEndpointData | undefined): AgentChatEndpoint {
  if (data === undefined) throw new Error("The agent chat endpoint is unavailable.");
  return v.parse(AgentChatEndpointSchema, data.agentChatEndpoint);
}

/** Render the `<system_context>` block for the current view, or "" on failure. */
async function renderContext(
  renderPrompt: (
    variables: RenderAgentPromptVariables,
  ) => Promise<RenderAgentPromptData | undefined>,
  agentId: string,
  view: AgentChatView,
): Promise<string> {
  const data = await renderPrompt({ id: agentId, view });
  return data?.renderAgentPrompt ?? "";
}

/** Convert the endpoint's MCP server map to the ACP `newSession` array form. */
function toMcpServers(servers: Record<string, McpServerConfig>): McpServer[] {
  return Object.entries(servers).map(([name, config]) => ({
    type: "http",
    name,
    url: config.url,
    headers: Object.entries(config.headers ?? {}).map(([key, value]) => ({ name: key, value })),
  }));
}

/**
 * Fold one session update into `log`, returning a NEW array whose trailing assistant
 * message is a fresh object (new identity), so assistant-ui re-renders the stream. An
 * update that changes nothing returns `log` unchanged, avoiding a needless re-render.
 */
function foldIntoLog(log: ChatMessage[], note: SessionNotification): ChatMessage[] {
  const last = log[log.length - 1];
  const isAssistant = last !== undefined && last.role === "assistant";
  const base: ChatMessage = isAssistant
    ? last
    : { id: `assistant-${log.length}`, role: "assistant", text: "", toolCalls: [] };
  const next = applyUpdate(base, note);
  if (next === base) return log;
  return isAssistant ? [...log.slice(0, -1), next] : [...log, next];
}

/**
 * Apply one session update to `assistant`, returning a new message — text chunks append,
 * tool calls upsert, all immutably — or the same reference when the update is not rendered.
 */
function applyUpdate(assistant: ChatMessage, note: SessionNotification): ChatMessage {
  const update = note.update;
  switch (update.sessionUpdate) {
    case "agent_message_chunk":
    case "agent_thought_chunk":
      return update.content.type === "text"
        ? { ...assistant, text: assistant.text + update.content.text }
        : assistant;
    case "tool_call":
      return {
        ...assistant,
        toolCalls: [
          ...assistant.toolCalls,
          { id: update.toolCallId, title: update.title, status: update.status ?? "pending" },
        ],
      };
    case "tool_call_update": {
      const block = assistant.toolCalls.find((call) => call.id === update.toolCallId);
      if (block === undefined) return assistant;
      const toolCalls = assistant.toolCalls.map((call) =>
        call.id === update.toolCallId
          ? {
              ...call,
              title: typeof update.title === "string" ? update.title : call.title,
              status: update.status ?? call.status,
            }
          : call,
      );
      return { ...assistant, toolCalls };
    }
    default:
      return assistant;
  }
}

/** Extract the plain text of a composer message. */
function textOf(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}
