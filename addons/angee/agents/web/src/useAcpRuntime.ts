// The ACP chat runtime: opens a forward-authed WebSocket to a running agent, drives
// the ACP session (initialize → newSession → setModel → prompt/cancel), folds `session/update`
// notifications into an assistant-ui external store, and renders the result through
// `AssistantRuntimeProvider`. The browser speaks ACP to the agent through the
// operator's central Caddy; the route token is short-lived, so the endpoint is
// re-minted and the socket reconnected when the token nears expiry.

import * as React from "react";
import {
  SimpleImageAttachmentAdapter,
  useExternalStoreRuntime,
  type AppendMessage,
  type CompleteAttachment,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  ClientSideConnection,
  PROTOCOL_VERSION,
  type Agent,
  type AvailableCommand,
  type Client,
  type ContentBlock,
  type McpServer,
  type NewSessionResponse,
  type PromptCapabilities,
  type RequestPermissionRequest,
  type RequestPermissionResponse,
  type SessionConfigSelectGroup,
  type SessionConfigSelectOption,
  type SessionNotification,
} from "@agentclientprotocol/sdk";
import * as v from "valibot";
import { useAuthoredMutation } from "@angee/ui";
import type { DocumentVariables } from "@angee/refine";

import { messageOf } from "./acp-error";
import { foldIntoLog, type ChatMessage, type ChatPart } from "./acp-log";
import { emptySession, foldIntoSession, type AcpSession } from "./acp-session";
import { openAcpTransport, type AcpTransport } from "./acp-transport";
import { type DocumentType } from "@angee/gql/console";
import {
  AgentChatEndpointMutation,
  AgentChatEndpointSchema,
  RenderAgentPrompt,
  type AgentChatEndpoint,
  type AgentChatView,
  type McpServerConfig,
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
  /** The model handle selected on the Agent row and applied to the ACP session. */
  modelHandle: string;
  /** The agent's advertised slash commands (from `available_commands_update`), for the
   *  composer's `/` palette; empty until the agent advertises any. */
  availableCommands: readonly AvailableCommand[];
  /** Whether the agent advertises `promptCapabilities.image` — gates the composer's image
   *  attachment controls (the paperclip + the attachment adapter). */
  imageSupported: boolean;
  /** Whether the user's current view rides along as the leading context block on each send.
   *  Default true; clearing the view-record chip suppresses the context block. */
  recordAttached: boolean;
  /** Re-attach the current view as the send-time context (sets `recordAttached`). */
  attachRecord: () => void;
  /** Drop the current view from the send (clears `recordAttached`, suppressing context). */
  clearRecord: () => void;
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
  const [modelHandle, setModelHandle] = React.useState("");
  // Whether the agent advertises `promptCapabilities.image` — STATE (not a ref) because it
  // arrives async after `initialize`, and the attachment-adapter memo + the composer's
  // paperclip must re-render when it flips on. `promptCapabilitiesRef` below carries the same
  // facts to `onNew` for send-time correctness.
  const [imageSupported, setImageSupported] = React.useState(false);
  // Whether the user's current view rides along as the leading context block. Default true so
  // context is on by default; clearing the view-record chip flips it off. A ref mirror lets the
  // send-time `onNew` read the latest value without re-subscribing the callback.
  const [recordAttached, setRecordAttached] = React.useState(true);
  const recordAttachedRef = React.useRef(recordAttached);
  recordAttachedRef.current = recordAttached;
  // Latent session state (the agent's advertised slash commands today) folded from `session/update`
  // alongside the transcript; held outside the message log because it is not a transcript part.
  const [session, setSession] = React.useState<AcpSession>(emptySession);
  // Bumping this re-runs the connect effect — the `reconnect()` control.
  const [reconnectNonce, setReconnectNonce] = React.useState(0);

  const connectionRef = React.useRef<Agent | null>(null);
  const transportRef = React.useRef<AcpTransport | null>(null);
  const sessionIdRef = React.useRef<string | null>(null);
  // The agent's advertised prompt capabilities (from `initialize`), read by `onNew` to pick
  // the native context-block shape; a ref so a fresh value never re-triggers the connect effect.
  const promptCapabilitiesRef = React.useRef<PromptCapabilities | null>(null);
  const viewRef = React.useRef(view);
  viewRef.current = view;

  const [mintEndpoint] = useAuthoredMutation(AgentChatEndpointMutation);
  const [renderPrompt] = useAuthoredMutation(RenderAgentPrompt);

  // Fold one `session/update` into the log as a NEW object: assistant-ui caches converted
  // messages by source identity, so a fresh object per chunk is what makes streamed text
  // re-render (an in-place mutation keeps the identity and is dropped).
  const onUpdate = React.useCallback((note: SessionNotification): void => {
    setMessages((log) => foldIntoLog(log, note));
    setSession((s) => foldIntoSession(s, note));
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
    setMcpServers({});
    setModelHandle("");
    setImageSupported(false);
    setRecordAttached(true);
    promptCapabilitiesRef.current = null;

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
      // Reset commands on EVERY (re)connect — both the hard reconnect AND the silent
      // token-refresh connect(true), which does not re-run the effect (so the effect-top resets
      // don't cover it) — so a stale palette never outlives its session. Race-safe at connect-start:
      // no available_commands_update can arrive before the session handshake below.
      setSession(emptySession);
      try {
        const endpoint = await mintEndpoint({ id: agentId });
        if (!active) return;
        const validated = parseEndpoint(endpoint);
        setMcpServers(validated.mcp_servers);
        setModelHandle(validated.model_handle);
        const transport = openAcpTransport(validated.url, validated.token);
        transportRef.current = transport;
        const connection = new ClientSideConnection(() => makeClient(onUpdateRef), transport.stream);
        connectionRef.current = connection;
        await transport.ready;
        if (!active) return;
        const init = await connection.initialize({
          protocolVersion: PROTOCOL_VERSION,
          clientCapabilities: {},
        });
        if (!active) return;
        const caps = init.agentCapabilities?.promptCapabilities ?? null;
        promptCapabilitiesRef.current = caps;
        setImageSupported(caps?.image === true);
        // Straight to a session: the agent runtime authenticates from provisioned
        // container env (ANTHROPIC_API_KEY or OAuth bearer env), and its ACP
        // `authenticate` method is not implemented — it advertises `claude-login`
        // only as a terminal hint. A genuinely unauthenticated agent fails
        // `newSession` here, which the catch below surfaces, rather than hanging.
        const session = await connection.newSession({
          cwd: "/workspace",
          mcpServers: toMcpServers(validated.mcp_servers),
        });
        if (!active) return;
        await selectSessionModel(connection, session, validated.model_handle);
        if (!active) return;
        sessionIdRef.current = session.sessionId;
        setStatus("ready");
        scheduleRefresh(validated.expires_at);
        // Only the *current* transport's close means the chat dropped — a scheduled
        // refresh closes the previous socket itself, and that close must not clobber the
        // freshly reconnected one.
        void transport.closed.then(() => {
          if (active && transportRef.current === transport) setStatus("closed");
        });
      } catch (caught) {
        if (!active) return;
        setStatus("error");
        setError(messageOf(caught, "Failed to connect to the agent."));
      }
    };

    // Re-mint the token and reconnect a margin before it expires; an unparseable or past
    // `expires_at` simply skips the refresh, leaving the connect-once socket in place.
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

  // The attachment adapter, wired onto the runtime only when the agent advertises `image`.
  // `SimpleImageAttachmentAdapter` reads pasted/picked images into data-URL parts; `onNew`
  // maps those to ACP `image` ContentBlocks. Memoized on `imageSupported` so the runtime sees
  // a stable adapter that appears/disappears with capability.
  const attachmentAdapter = React.useMemo(
    () => (imageSupported ? new SimpleImageAttachmentAdapter() : undefined),
    [imageSupported],
  );

  const onNew = React.useCallback(
    async (message: AppendMessage): Promise<void> => {
      const connection = connectionRef.current;
      const sessionId = sessionIdRef.current;
      const userText = textOf(message);
      // Map the composer's image attachments to ACP blocks once; the send is valid with text OR
      // attachments, so an image-only paste must not be dropped by the early return.
      const blocks = attachmentBlocks(message.attachments, promptCapabilitiesRef.current);
      if (connection === null || sessionId === null) return;
      if (userText === "" && blocks.length === 0) return;

      const echoParts: ChatPart[] = [];
      if (userText !== "") echoParts.push({ kind: "text", text: userText });
      // Echo the user's image in the transcript from the same attachments, keeping the data-URL
      // verbatim for an `<img src>` (the ACP block above carries the raw base64 instead).
      for (const attachment of message.attachments ?? []) {
        for (const part of attachment.content) {
          if (part.type === "image") {
            echoParts.push({ kind: "image", image: part.image, filename: part.filename });
          }
        }
      }
      setMessages((log) => [
        ...log,
        { id: `user-${log.length}`, role: "user", parts: echoParts },
      ]);
      setIsRunning(true);
      try {
        // The view-record chip's presence gates the leading context block: cleared ⇒ no context.
        // `buildPromptBlocks` omits an empty-string context, so this needs no extra branch there.
        const context = recordAttachedRef.current
          ? await fetchSystemContext(renderPrompt, agentId, viewRef.current)
          : "";
        const prompt = buildPromptBlocks(context, userText, promptCapabilitiesRef.current, blocks);
        await connection.prompt({ sessionId, prompt });
      } catch (caught) {
        setError(messageOf(caught, "The agent did not respond."));
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
  const attachRecord = React.useCallback((): void => setRecordAttached(true), []);
  const clearRecord = React.useCallback((): void => setRecordAttached(false), []);
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
    adapters: { attachments: attachmentAdapter },
  });

  return {
    runtime,
    status,
    error,
    reconnect,
    clear,
    mcpServers,
    modelHandle,
    availableCommands: session.availableCommands,
    imageSupported,
    recordAttached,
    attachRecord,
    clearRecord,
    renderContext,
  };
}

/** Convert a stored chat message to the assistant-ui thread shape: each part maps to the
 *  matching assistant-ui content part. Tool input/status/result/isError ride the tool
 *  part's `args` bag (its typed JSON input slot), which the view's `ToolPart` reads. */
function convertMessage(message: ChatMessage): ThreadMessageLike {
  const content = message.parts.map((part) => {
    switch (part.kind) {
      case "text":
        return { type: "text" as const, text: part.text };
      case "reasoning":
        return { type: "reasoning" as const, text: part.text };
      case "image":
        return { type: "image" as const, image: part.image, filename: part.filename };
      case "tool":
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
      default: {
        // Exhaustive: a new ChatPart kind must add a case above, or this fails to compile.
        const exhaustive: never = part;
        return exhaustive;
      }
    }
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
function parseEndpoint(
  data: DocumentType<typeof AgentChatEndpointMutation> | undefined,
): AgentChatEndpoint {
  if (data === undefined) throw new Error("The agent chat endpoint is unavailable.");
  return v.parse(AgentChatEndpointSchema, data.agent_chat_endpoint);
}

/** Render the `<system_context>` block for the current view, or "" on failure. */
async function fetchSystemContext(
  renderPrompt: (
    variables: DocumentVariables<typeof RenderAgentPrompt>,
  ) => Promise<DocumentType<typeof RenderAgentPrompt> | undefined>,
  agentId: string,
  view: AgentChatView,
): Promise<string> {
  try {
    const data = await renderPrompt({ id: agentId, view });
    return data?.render_agent_prompt ?? "";
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

/** Select the Agent row's model for the ACP session before the first prompt. In sdk 1.0.0 the
 *  model is a session config option (`category: "model"`), applied via `setSessionConfigOption`. */
export async function selectSessionModel(
  connection: Agent,
  session: NewSessionResponse,
  modelHandle: string,
): Promise<void> {
  if (modelHandle === "") return;
  const option = session.configOptions?.find((opt) => opt.category === "model");
  // No model config option means the agent owns its own model (env/config-pinned in its
  // container, e.g. opencode) — defer rather than failing the whole session.
  if (option === undefined || option.type !== "select") return;
  // The select's values are either flat or grouped; flatten to the selectable options.
  const values = (
    option.options as ReadonlyArray<SessionConfigSelectOption | SessionConfigSelectGroup>
  ).flatMap((entry) => ("options" in entry ? entry.options : [entry]));
  const match = values.find((value) => value.value === modelHandle || value.name === modelHandle);
  if (match === undefined) {
    const available = values.map((value) => value.value).join(", ") || "none";
    throw new Error(`The selected model ${modelHandle} is not available in this agent session (${available}).`);
  }
  if (option.currentValue === match.value) return;
  if (connection.setSessionConfigOption === undefined) {
    throw new Error(`The agent does not support selecting ${modelHandle} for this session.`);
  }
  await connection.setSessionConfigOption({
    sessionId: session.sessionId,
    configId: option.id,
    value: match.value,
  });
}

/** Opaque identifier for the embedded view-context resource (its content is inline). */
const CONTEXT_RESOURCE_URI = "angee:///agent/system-context";

/**
 * Build the ACP prompt for one send. Context and the user's text are each their OWN `ContentBlock`
 * (context as an embedded `resource` when the agent advertises `embeddedContext`, else a plain
 * `text` block) — never string-merged. A `/command` send carries NO context block: claude-agent-acp
 * runs a slash command only when the message is a clean "/command" — any extra block (an embedded
 * resource becomes a URI-link text block in the SDK message) makes the SDK treat it as prose and
 * invoke the model instead. A normal send leads with context. Attachments always trail; the empty
 * user-text block is omitted so an image-only send carries only its image.
 */
export function buildPromptBlocks(
  context: string,
  userText: string,
  capabilities: PromptCapabilities | null,
  attachments: ContentBlock[] = [],
): ContentBlock[] {
  // A slash command must reach the agent as a clean "/command" message, so it carries no context.
  const contextBlock: ContentBlock | null =
    userText.startsWith("/") || context === ""
      ? null
      : capabilities?.embeddedContext === true
        ? { type: "resource", resource: { uri: CONTEXT_RESOURCE_URI, text: context, mimeType: "text/markdown" } }
        : { type: "text", text: context };
  const blocks: ContentBlock[] = [];
  if (contextBlock !== null) blocks.push(contextBlock);
  if (userText !== "") blocks.push({ type: "text", text: userText });
  blocks.push(...attachments);
  return blocks;
}

/**
 * Map the composer's attachments to ACP `image` ContentBlocks for the prompt, gated on the
 * agent advertising `image`. Only image parts are mapped; non-image (file) parts are skipped —
 * the storage `resource_link` path (an agent-reachable URI minted by the storage addon) is a
 * deferred follow-up, not re-uploaded from the chat addon.
 */
export function attachmentBlocks(
  attachments: readonly CompleteAttachment[] | undefined,
  capabilities: PromptCapabilities | null,
): ContentBlock[] {
  if (attachments === undefined || capabilities?.image !== true) return [];
  const blocks: ContentBlock[] = [];
  for (const attachment of attachments) {
    for (const part of attachment.content) {
      if (part.type !== "image") continue;
      const block = dataUrlToImageBlock(part.image);
      if (block !== null) blocks.push(block);
    }
  }
  return blocks;
}

/** Split a `data:<mime>;base64,<data>` URL (what `SimpleImageAttachmentAdapter` yields) into an
 *  ACP `image` block, whose `data` is RAW base64 — not a data: URL — paired with its mime type.
 *  Returns null for anything that is not a base64 data URL. */
export function dataUrlToImageBlock(dataUrl: string): ContentBlock | null {
  const match = /^data:([^;]+);base64,(.*)$/s.exec(dataUrl);
  if (match === null) return null;
  const [, mimeType, data] = match;
  if (mimeType === undefined || data === undefined) return null;
  return { type: "image", data, mimeType };
}

/** Extract the plain text of a composer message. */
function textOf(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}
