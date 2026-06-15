// The ACP chat runtime: opens a forward-authed WebSocket to a running agent, drives
// the ACP session (initialize → newSession → prompt/cancel), folds `session/update`
// notifications into an assistant-ui external store, and renders the result through
// `AssistantRuntimeProvider`. The browser speaks ACP to the agent through the
// operator's central Caddy; the route token is short-lived, so the endpoint is
// re-minted and the socket reconnected when the token nears expiry.

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
} from "@zed-industries/agent-client-protocol";
import * as v from "valibot";
import { useAuthoredMutation } from "@angee/sdk";

import { foldIntoLog, type ChatMessage } from "./acp-log";
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

export interface AcpRuntime {
  runtime: ReturnType<typeof useExternalStoreRuntime>;
  status: AcpStatus;
  error: string | null;
  /** Tear the socket down and open a fresh session (resets the transcript). */
  reconnect: () => void;
  /** Clear the transcript without dropping the session. */
  clear: () => void;
  /** The agent's advertised MCP servers, for the session info panel. */
  mcpServers: Record<string, McpServerConfig>;
  /** Render the `<system_context>` for the current view, for the session info panel. */
  renderContext: () => Promise<string>;
}

/**
 * Build the assistant-ui runtime for chatting with the agent identified by `agentId`,
 * about the user's open `view`.
 *
 * Holds the message log as immutable React state: each `session/update` replaces the
 * in-flight assistant message with a fresh object (with fresh parts), so assistant-ui's
 * identity-keyed message cache re-renders the streamed text/reasoning/tool calls.
 */
export function useAcpRuntime(agentId: string, view: AgentChatView): AcpRuntime {
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [status, setStatus] = React.useState<AcpStatus>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [isRunning, setIsRunning] = React.useState(false);
  const [mcpServers, setMcpServers] = React.useState<Record<string, McpServerConfig>>({});
  // Bumping this re-runs the connect effect — the `reconnect()` control.
  const [reconnectNonce, setReconnectNonce] = React.useState(0);

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
  // `agentId`/`reconnectNonce` alone).
  const onUpdateRef = React.useRef(onUpdate);
  onUpdateRef.current = onUpdate;

  // Connect on mount; reconnect before the route token expires or when `reconnect()` is
  // called; tear the socket down on unmount or agent change. A per-effect `active` flag
  // gates every state update and the post-await continuations, so an in-flight connect for
  // a stale agent never clobbers the live one (it resolves after cleanup set `active = false`).
  React.useEffect(() => {
    let active = true;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    // A new agent (or an explicit reconnect) starts from an empty transcript; an in-effect
    // token-refresh reconnect does not re-run the effect, so it keeps the live conversation.
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
        setMcpServers(validated.mcpServers);
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
  }, [agentId, mintEndpoint, reconnectNonce]);

  const onNew = React.useCallback(
    async (message: AppendMessage): Promise<void> => {
      const connection = connectionRef.current;
      const sessionId = sessionIdRef.current;
      const userText = textOf(message);
      if (connection === null || sessionId === null || userText === "") return;

      setMessages((log) => [
        ...log,
        { id: `user-${log.length}`, role: "user", parts: [{ kind: "text", text: userText }] },
      ]);
      setIsRunning(true);
      try {
        const context = await fetchSystemContext(renderPrompt, agentId, viewRef.current);
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

  const reconnect = React.useCallback((): void => setReconnectNonce((nonce) => nonce + 1), []);
  const clear = React.useCallback((): void => setMessages([]), []);
  const renderContext = React.useCallback(
    (): Promise<string> => fetchSystemContext(renderPrompt, agentId, viewRef.current),
    [agentId, renderPrompt],
  );

  const runtime = useExternalStoreRuntime({
    isRunning,
    messages,
    onNew,
    onCancel,
    convertMessage,
  });

  return { runtime, status, error, reconnect, clear, mcpServers, renderContext };
}

/** Convert a stored chat message to the assistant-ui thread shape: each part maps to the
 *  matching assistant-ui content part. Tool input/status/result/isError ride the tool
 *  part's `args` bag (its typed JSON input slot), which the view's `ToolPart` reads. */
function convertMessage(message: ChatMessage): ThreadMessageLike {
  const content = message.parts.map((part) => {
    if (part.kind === "text") return { type: "text" as const, text: part.text };
    if (part.kind === "reasoning") return { type: "reasoning" as const, text: part.text };
    return {
      type: "tool-call" as const,
      toolCallId: part.id,
      toolName: part.toolName,
      args: {
        status: part.status,
        input: part.input ?? null,
        result: part.result ?? null,
        isError: part.isError ?? false,
      },
      argsText: "",
    };
  });
  // The parts map cleanly onto assistant-ui text/reasoning/tool-call content; cast at this
  // boundary because the tool `args` bag carries `unknown` input/result that the JSON-typed
  // content part can't infer.
  return { id: message.id, role: message.role, content: content as ThreadMessageLike["content"] };
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
async function fetchSystemContext(
  renderPrompt: (
    variables: RenderAgentPromptVariables,
  ) => Promise<RenderAgentPromptData | undefined>,
  agentId: string,
  view: AgentChatView,
): Promise<string> {
  try {
    const data = await renderPrompt({ id: agentId, view });
    return data?.renderAgentPrompt ?? "";
  } catch {
    return "";
  }
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

/** Extract the plain text of a composer message. */
function textOf(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}
