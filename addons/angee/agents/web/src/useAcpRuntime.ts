// One assistant-ui runtime over the SDK native v1 and v2 ACP clients.
import * as React from "react";
import { createMessageQueue, SimpleImageAttachmentAdapter, useExternalStoreRuntime, type MessageQueueController, type AppendMessage, type CompleteAttachment } from "@assistant-ui/react";
import type { AvailableCommand, ContentBlock, McpServer, PromptCapabilities, RequestPermissionResponse } from "@agentclientprotocol/sdk";
import * as v from "valibot";
import { stableKey, useActiveDataProviderName, useAuthoredMutation, useInfiniteQuery, useQueryClient, type DocumentVariables } from "@angee/refine";
import { useLatestRef } from "@angee/ui";
import type { DocumentType } from "@angee/gql/console";

import { ACP_META_NAMESPACE, connectAcp, selectSessionModel, type AcpClient, type AcpPermissionRequest } from "./acp-client";
import { convertMessage, foldIntoLog, reconcileUserMessage, settleLog, type ChatMessage, type ChatPart } from "./acp-log";
import { emptySession, foldIntoSession, type AcpSession, type AcpSessionNavigation } from "./acp-session";
import { openAcpTransport, type AcpTransport } from "./acp-transport";
import { AgentChatEndpointMutation, AgentChatEndpointSchema, RenderAgentPrompt, agentChatViewInput, type AgentChatEndpoint, type AgentChatView, type McpServerConfig } from "./documents";
import { useAgentsT } from "./i18n";

const TOKEN_REFRESH_MARGIN_MS = 60_000;
const MAX_TIMER_MS = 2_147_483_647;
const MAX_RECONNECT_MS = 30_000;
class EndpointError extends Error {}
export type AcpStatus = "idle" | "connecting" | "ready" | "error" | "closed";
export interface AcpPermission { id: number; protocolVersion: 1 | 2; request: AcpPermissionRequest }
type RecordOverride = { viewKey: string; attached: boolean } | null;
export interface AcpRuntimeOptions {
  agentId: string;
  view: AgentChatView;
  /** Initial session for this agent binding; live roster changes do not reset it. */
  knownSessionId?: string;
  protocolVersion?: 1 | 2;
  showSessions?: boolean;
  onSessionChange?: (id: string) => void;
}
export interface AcpRuntime {
  runtime: ReturnType<typeof useExternalStoreRuntime>;
  protocolVersion: 1 | 2 | undefined;
  status: AcpStatus;
  error: string | null;
  reconnect: () => void;
  clear: () => void;
  mcpServers: Record<string, McpServerConfig>;
  modelHandle: string;
  availableCommands: readonly AvailableCommand[];
  imageSupported: boolean;
  recordAttached: boolean;
  attachRecord: () => void;
  clearRecord: () => void;
  renderContext: () => Promise<string>;
  sessions: AcpSessionNavigation;
  permissions: readonly AcpPermission[];
  answerPermission: (id: number, optionId: string, reason?: string) => void;
}

/**
 * One ACP runtime: v1 ends with the prompt response; v2 follows state_update and
 * accepts queued prompts. Known session ids are read once per agent binding.
 * Resume/load reconnect the same session; v1 resume retains the local transcript.
 *
 * A v2 requires_action → running update closes unanswered permission dialogs
 * when another tab answered first. We release the request as cancelled, without
 * submitting a second decision. Disconnect and unmount also release requests.
 * V2 rejection reasons travel on the selected outcome as _meta.angee.reason.
 * Ordered running/output/idle brackets attribute each turn to the oldest accepted,
 * unsettled user message. Queued acceptance never changes the foreground turn.
 * V2 sends the normalized view on session creation and, while Current view is
 * attached, on each prompt as _meta.angee.context. V1 renders prompt context.
 * Both versions auto-attach a view differing from the last locally sent context,
 * consume it on send, and allow a manual override until the view changes.
 * Reloaded replay has no normalized envelope: treat it as not yet sent. V2
 * reconnect preserves locally known sent envelopes by native message identity.
 */
export function useAcpRuntime(options: AcpRuntimeOptions): AcpRuntime {
  const { agentId, view, protocolVersion, showSessions = false } = options;
  const t = useAgentsT();
  const dataProviderName = useActiveDataProviderName();
  const queryClient = useQueryClient();
  const [messages, publishMessages] = React.useState<ChatMessage[]>([]);
  // The same log is available synchronously when the native queue advances
  // before React commits a render. Sent-view history lives only on its prompts.
  const messagesRef = React.useRef(messages);
  const setMessages = React.useCallback((update: React.SetStateAction<ChatMessage[]>) => {
    messagesRef.current = typeof update === "function" ? update(messagesRef.current) : update;
    publishMessages(messagesRef.current);
  }, []);
  const [status, setStatus] = React.useState<AcpStatus>("idle");
  const [error, setError] = React.useState<string | null>(null);
  const [v1Running, setV1Running] = React.useState(false);
  const [sending, setSending] = React.useState(false);
  const [pendingTurn, setPendingTurn] = React.useState(false);
  const [client, setClient] = React.useState<AcpClient | null>(null);
  const [endpoint, setEndpoint] = React.useState<AgentChatEndpoint | null>(null);
  const [session, setSession] = React.useState<AcpSession>(emptySession);
  const [activeSessionId, setActiveSessionId] = React.useState<string | null>(null);
  const [reconnectNonce, setReconnectNonce] = React.useState(0);
  const [recordOverride, publishRecordOverride] = React.useState<RecordOverride>(null);
  const recordOverrideRef = React.useRef<RecordOverride>(null);
  const setRecordOverride = React.useCallback((override: RecordOverride) => {
    recordOverrideRef.current = override;
    publishRecordOverride(override);
  }, []);
  const viewKey = stableKey(agentChatViewInput(view));
  if (recordOverride !== null && recordOverride.viewKey !== viewKey) setRecordOverride(null);
  const recordAttached = isRecordAttached(messages, activeSessionId, viewKey, recordOverride);
  const [permissions, setPermissions] = React.useState<AcpPermission[]>([]);
  const connectionRef = React.useRef<AcpClient | null>(null);
  const endpointRef = React.useRef<AgentChatEndpoint | null>(null);
  const sessionIdRef = React.useRef<string | null>(null);
  const sessionCwdRef = React.useRef("/workspace");
  const sessionRef = React.useRef(emptySession);
  const bindingRef = React.useRef<string | undefined>(undefined);
  // Native drafts have a local id before ACP creates their remote session.
  const threadIdRef = React.useRef(options.knownSessionId ?? `draft-${agentId}`);
  const optionsRef = useLatestRef(options);
  const pendingPermissions = React.useRef(new Map<number, { version: 1 | 2; resolve: (answer: RequestPermissionResponse) => void }>());
  const messageCounter = React.useRef(0);
  const permissionCounter = React.useRef(0);
  const sendingRef = React.useRef(false);
  const viewRef = useLatestRef(view);
  const statusRef = useLatestRef(status);
  const [mintEndpoint] = useAuthoredMutation(AgentChatEndpointMutation);
  const [renderPrompt] = useAuthoredMutation(RenderAgentPrompt);
  const queryKey = React.useMemo(() => [dataProviderName, "acp", "agents", agentId, "sessions"] as const, [dataProviderName, agentId]);
  const listOptions = {
    queryKey,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }: { pageParam: string | undefined; signal: AbortSignal }) => {
      const current = connectionRef.current;
      if (!current?.canList) throw new Error(t("sessions.unavailable"));
      return current.listSessions(pageParam, signal);
    },
    getNextPageParam: (page: Awaited<ReturnType<AcpClient["listSessions"]>>, _pages: unknown, pageParam: string | undefined, pageParams: Array<string | undefined>) => {
      const next = page.nextCursor;
      return next && next !== pageParam && !pageParams.includes(next) ? next : undefined;
    },
  };
  const navigationAvailable = client?.canList === true && client.canLoad;
  const sessionQuery = useInfiniteQuery({ ...listOptions, enabled: showSessions && status === "ready" && navigationAvailable });
  const queryRef = useLatestRef(sessionQuery);
  const dismissPermissions = React.useCallback(() => {
    for (const { resolve } of pendingPermissions.current.values()) resolve({ outcome: { outcome: "cancelled" } });
    pendingPermissions.current.clear();
    setPermissions([]);
  }, []);
  const refreshSessions = React.useCallback(() => { void queryClient.invalidateQueries({ queryKey }); }, [queryClient, queryKey]);
  const refreshRef = useLatestRef(refreshSessions);
  const listOptionsRef = useLatestRef(listOptions);

  React.useEffect(() => {
    let active = true;
    let generation = 0;
    let retry = 0;
    let transport: AcpTransport | null = null;
    let replayLog: ChatMessage[] | null = null;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    if (bindingRef.current !== agentId) {
      sessionIdRef.current = optionsRef.current.knownSessionId ?? null;
      threadIdRef.current = sessionIdRef.current ?? `draft-${agentId}`;
      sessionCwdRef.current = "/workspace";
      setMessages([]);
      setActiveSessionId(sessionIdRef.current);
      setRecordOverride(null);
      bindingRef.current = agentId;
    }
    const tearDown = (): void => {
      clearTimeout(refreshTimer);
      clearTimeout(reconnectTimer);
      connectionRef.current?.close();
      connectionRef.current = null;
      const previousTransport = transport;
      transport = null;
      previousTransport?.close();
      dismissPermissions();
    };
    const scheduleRetry = (): void => {
      const delay = Math.min(1000 * 2 ** retry++, MAX_RECONNECT_MS);
      reconnectTimer = setTimeout(() => void connect(), delay);
    };
    const connect = async (silent = false): Promise<void> => {
      ++generation;
      tearDown();
      const currentGeneration = generation;
      const current = () => active && generation === currentGeneration;
      if (!silent) {
        setStatus("connecting");
        setClient(null);
        setPendingTurn(false);
        sessionRef.current = emptySession;
        setSession(emptySession);
      }
      setError(null);
      try {
        const data = await mintEndpoint({ id: agentId });
        if (!current()) return;
        const validated = parseEndpoint(data, t("chat.unsupportedProtocol"), t("chat.connectFailed"), optionsRef.current.protocolVersion);
        endpointRef.current = validated;
        setEndpoint(validated);
        const opened = openAcpTransport(validated.url, validated.token, validated.protocol_version);
        transport = opened;
        // Watch closures during initialize/restore as well as after ready.
        void opened.closed.then(() => {
          if (!current() || transport !== opened) return;
          ++generation;
          tearDown();
          setStatus("closed");
          scheduleRetry();
        });
        const connection = await connectAcp(opened, (note) => {
          if (!current() || (sessionIdRef.current !== null && note.params.sessionId !== sessionIdRef.current)) return;
          const previousState = sessionRef.current.state;
          const next = foldIntoSession(sessionRef.current, note.params);
          sessionRef.current = next;
          setSession(next);
          if (replayLog !== null) replayLog = foldIntoLog(replayLog, note, t("chat.turnFailed"), t("chat.turnStopped"));
          else setMessages((log) => foldIntoLog(log, note, t("chat.turnFailed"), t("chat.turnStopped")));
          if (note.params.update.sessionUpdate === "state_update") setPendingTurn(false);
          if (opened.protocolVersion === 2 && previousState === "requires_action" && next.state !== "requires_action") dismissPermissions();
          if (replayLog === null && statusRef.current === "ready" && (note.params.update.sessionUpdate === "session_info_update" || (previousState !== "idle" && next.state === "idle"))) refreshRef.current();
        }, (request) => {
          if (!current() || (sessionIdRef.current !== null && request.sessionId !== sessionIdRef.current)) return Promise.resolve({ outcome: { outcome: "cancelled" } });
          const id = ++permissionCounter.current;
          return new Promise((resolve) => {
            pendingPermissions.current.set(id, { version: opened.protocolVersion, resolve });
            setPermissions((pending) => [...pending, { id, protocolVersion: opened.protocolVersion, request }]);
          });
        });
        if (!current()) { connection.close(); opened.close(); return; }
        connectionRef.current = connection;
        setClient(connection);
        if (!sessionIdRef.current && connection.canList && connection.canRestore) {
          const listed = await queryClient.fetchInfiniteQuery({ ...listOptionsRef.current, staleTime: 0 });
          if (!current()) return;
          const newest = listed.pages[0]?.sessions[0];
          if (newest) { sessionIdRef.current = newest.sessionId; sessionCwdRef.current = newest.cwd; }
        }
        const sessionId = sessionIdRef.current;
        if (sessionId && connection.canRestore) {
          if (connection.canLoad) replayLog = [];
          await connection.restoreSession({ sessionId, cwd: sessionCwdRef.current, mcpServers: toMcpServers(validated.mcp_servers) });
        } else if (sessionId) {
          // An agent without restoration needs a new session on its next send.
          // Its retained local transcript survives silent token rotation.
          sessionIdRef.current = null;
        }
        if (!current()) return;
        if (replayLog !== null) {
          const replayed = replayLog;
          setMessages((log) => [...replayed.map((message) => {
            const local = log.find((entry) => entry.id === message.id);
            return local?.sentContext ? { ...message, sentContext: local.sentContext } : message;
          }), ...log.filter((message) => message.deliveryFailed)]);
          replayLog = null;
        }
        setActiveSessionId(sessionIdRef.current);
        setStatus("ready");
        retry = 0;
        const expires = Date.parse(validated.expires_at);
        const refresh = () => {
          const delay = expires - Date.now() - TOKEN_REFRESH_MARGIN_MS;
          if (!Number.isFinite(delay)) return;
          if (delay > MAX_TIMER_MS) refreshTimer = setTimeout(refresh, MAX_TIMER_MS);
          else if (delay > 0) refreshTimer = setTimeout(() => void connect(connection.protocolVersion === 1), delay);
        };
        refresh();
      } catch (caught) {
        if (!current()) return;
        ++generation;
        tearDown();
        if (caught instanceof EndpointError) { setStatus("error"); setError(caught.message); }
        else { setStatus("connecting"); setError(t("chat.connectFailed")); scheduleRetry(); }
      }
    };
    void connect();
    return () => { active = false; ++generation; tearDown(); };
  }, [agentId, protocolVersion, reconnectNonce, mintEndpoint, t, dismissPermissions, queryClient, setMessages, setRecordOverride]);

  const createSession = React.useCallback(async (): Promise<string> => {
    const connection = connectionRef.current;
    const config = endpointRef.current;
    if (!connection || !config) throw new Error(t("chat.responseFailed"));
    const created = await connection.newSession({ cwd: "/workspace", mcpServers: toMcpServers(config.mcp_servers),
      ...(connection.protocolVersion === 2 ? { _meta: { [ACP_META_NAMESPACE]: { context: agentChatViewInput(viewRef.current) } } } : {}),
    });
    if (connectionRef.current !== connection) throw new Error(t("chat.responseFailed"));
    sessionIdRef.current = created.sessionId;
    sessionCwdRef.current = "/workspace";
    await selectSessionModel(connection, created, config.model_handle, t("chat.modelUnavailable", { model: config.model_handle }));
    refreshRef.current();
    return created.sessionId;
  }, [t]);
  const imageSupported = client?.promptCapabilities.image === true;
  const attachmentAdapter = React.useMemo(() => imageSupported ? new SimpleImageAttachmentAdapter() : undefined, [imageSupported]);
  const onNew = React.useCallback(async (message: AppendMessage): Promise<void> => {
    const userText = textOf(message);
    const connection = connectionRef.current;
    const restoreComposer = () => {
      const composer = runtimeRef.current.thread.composer;
      if (!composer.getState().text) composer.setText(userText);
      for (const attachment of message.attachments ?? []) {
        if (!composer.getState().attachments.some((item) => item.id === attachment.id)) {
          void composer.addAttachment(attachment).catch(() => setError(t("chat.messageNotSent")));
        }
      }
    };
    const optimisticId = `user-local-${++messageCounter.current}`;
    const echoParts: ChatPart[] = [];
    if (userText !== "") echoParts.push({ kind: "text", text: userText });
    for (const attachment of message.attachments ?? []) {
      for (const part of attachment.content) if (part.type === "image") echoParts.push({ kind: "image", image: part.image, filename: part.filename });
    }
    if (echoParts.length === 0) return;
    if (!connection || statusRef.current !== "ready" || sendingRef.current || (connection.protocolVersion === 1 && v1RunningRef.current)) {
      setMessages((log) => [...log, { id: optimisticId, role: "user", deliveryFailed: true, parts: echoParts }]);
      setError(t("chat.messageNotSent"));
      restoreComposer();
      return;
    }
    const blocks = attachmentBlocks(message.attachments, connection.promptCapabilities);
    const promptView = agentChatViewInput(viewRef.current);
    const promptViewKey = stableKey(promptView);
    const override = recordOverrideRef.current;
    const attached = isRecordAttached(messagesRef.current, sessionIdRef.current, promptViewKey, override);
    const promptContext = attached ? promptView : undefined;
    setMessages((log) => [...log, { id: optimisticId, role: "user", optimistic: true, parts: echoParts }]);
    setError(null);
    sendingRef.current = true;
    setSending(true);
    if (connection.protocolVersion === 1) setV1Running(true);
    else setPendingTurn(true);
    let sent = false;
    let consumedOverride: RecordOverride | undefined;
    try {
      const sessionId = sessionIdRef.current ?? await createSession();
      setActiveSessionId(sessionId);
      const context = connection.protocolVersion === 1 && attached ? await fetchSystemContext(renderPrompt, agentId, promptView) : "";
      if (connectionRef.current !== connection || sessionIdRef.current !== sessionId) throw new Error();
      sent = true;
      const response = connection.prompt({
        sessionId, prompt: buildPromptBlocks(context, userText, connection.promptCapabilities, blocks),
        ...(connection.protocolVersion !== 2 || promptContext === undefined ? {} : { _meta: { [ACP_META_NAMESPACE]: { context: promptContext } } }),
      });
      // Record only context actually carried on the wire (v1 slash commands
      // and an empty render have no context block). Failed deliveries are ignored.
      if (attached) {
        const carriesContext = connection.protocolVersion === 2 || (context !== "" && !userText.startsWith("/"));
        if (carriesContext) setMessages((log) => log.map((entry) => entry.id === optimisticId ? { ...entry, sentContext: { sessionId, view: promptView } } : entry));
        if (recordOverrideRef.current === override) {
          // Even when the carrier omits context, sending consumes this badge.
          consumedOverride = carriesContext ? null : { viewKey: promptViewKey, attached: false };
          setRecordOverride(consumedOverride);
        }
      }
      const result = await response;
      if (sessionIdRef.current !== sessionId) return;
      if (result.messageId !== undefined) setMessages((log) => reconcileUserMessage(log, optimisticId, result.messageId));
      else setMessages((log) => settleLog(log, result.stopReason, t("chat.turnFailed"), t("chat.turnStopped"), optimisticId));
    } catch {
      setMessages((log) => log.map((entry) => entry.id === optimisticId ? { ...entry, optimistic: false, deliveryFailed: true } : entry));
      if (consumedOverride !== undefined && recordOverrideRef.current === consumedOverride && stableKey(agentChatViewInput(viewRef.current)) === promptViewKey) {
        setRecordOverride({ viewKey: promptViewKey, attached: true });
      }
      setError(t(sent ? "chat.responseFailed" : "chat.messageNotSent"));
      restoreComposer();
      if (sessionRef.current.state === "idle") setPendingTurn(false);
    } finally {
      sendingRef.current = false;
      setSending(false);
      if (connection.protocolVersion === 1) { setV1Running(false); dismissPermissions(); refreshRef.current(); }
    }
  }, [agentId, createSession, renderPrompt, t, dismissPermissions, setMessages, setRecordOverride]);
  const onCancel = React.useCallback(async (): Promise<void> => {
    const connection = connectionRef.current;
    const sessionId = sessionIdRef.current;
    if (!connection || !sessionId) return;
    try {
      await connection.cancel(sessionId);
      if (connectionRef.current === connection && connection.protocolVersion === 1) { setV1Running(false); dismissPermissions(); }
    } catch { setError(t("chat.responseFailed")); }
  }, [dismissPermissions, t]);
  const reconnect = React.useCallback(() => setReconnectNonce((nonce) => nonce + 1), []);
  const clear = React.useCallback(() => setMessages([]), [setMessages]);
  const attachRecord = React.useCallback(() => setRecordOverride({ viewKey: stableKey(agentChatViewInput(viewRef.current)), attached: true }), [setRecordOverride]);
  const clearRecord = React.useCallback(() => setRecordOverride({ viewKey: stableKey(agentChatViewInput(viewRef.current)), attached: false }), [setRecordOverride]);
  const renderContext = React.useCallback(() => fetchSystemContext(renderPrompt, agentId, agentChatViewInput(viewRef.current)), [agentId, renderPrompt]);
  const bindSession = React.useCallback((id: string, cwd: string) => {
    sessionIdRef.current = id;
    threadIdRef.current = id;
    sessionCwdRef.current = cwd;
    setMessages([]);
    setRecordOverride(null);
    setPendingTurn(false);
    setV1Running(false);
    sessionRef.current = emptySession;
    setSession(emptySession);
    dismissPermissions();
    setActiveSessionId(id);
  }, [dismissPermissions, setMessages, setRecordOverride]);
  const selectSession = React.useCallback((id: string, cwd: string) => {
    if (id === sessionIdRef.current) return;
    bindSession(id, cwd);
    reconnect();
  }, [bindSession, reconnect]);
  const newSession = React.useCallback(() => {
    if (sendingRef.current || statusRef.current !== "ready") return;
    void createSession().then((id) => {
      bindSession(id, "/workspace");
      optionsRef.current.onSessionChange?.(id);
    }).catch(() => setError(t("chat.responseFailed")));
  }, [createSession, bindSession, t]);
  const answerPermission = React.useCallback((id: number, optionId: string, reason?: string) => {
    const pending = pendingPermissions.current.get(id);
    if (!pending) return;
    pendingPermissions.current.delete(id);
    const text = reason?.trim();
    pending.resolve({ outcome: { outcome: "selected", optionId, ...(pending.version === 2 && text ? { _meta: { [ACP_META_NAMESPACE]: { reason: text } } } : {}) } });
    setPermissions((items) => items.filter((permission) => permission.id !== id));
  }, []);
  const v1RunningRef = useLatestRef(v1Running);
  const onNewRef = useLatestRef(onNew);
  const queueRef = React.useRef<MessageQueueController | null>(null);
  // The native queue serializes insertion acknowledgements only. ACP owns queued
  // turns, so release the next send after prompt acceptance, not after idle.
  const [queue] = React.useState(() => createMessageQueue({
    run: (message) => { void onNewRef.current(message).finally(() => queueRef.current?.notifyIdle()); },
  }));
  queueRef.current = queue;
  React.useSyncExternalStore(queue.subscribe, () => queue.adapter.items, () => queue.adapter.items);
  const runtime = useExternalStoreRuntime({
    isRunning: client?.protocolVersion === 2 ? session.state !== "idle" || pendingTurn : v1Running,
    isDisabled: status !== "ready", isSendDisabled: sending || (client?.protocolVersion === 1 && v1Running),
    messages, onNew, onCancel, convertMessage, adapters: { attachments: attachmentAdapter,
      threadList: { threadId: threadIdRef.current, threads: [{ id: threadIdRef.current,
        remoteId: activeSessionId ?? undefined, status: "regular" }],
      },
    },
    queue: client?.protocolVersion === 2 ? queue.adapter : undefined,
  });
  const runtimeRef = useLatestRef(runtime);
  const sessions = React.useMemo<AcpSessionNavigation>(() => ({
    currentId: activeSessionId, available: navigationAvailable,
    ready: status === "ready" && !sending, items: sessionQuery.data?.pages.flatMap((page) => page.sessions) ?? [],
    loading: sessionQuery.isFetching && !sessionQuery.data,
    hasMore: navigationAvailable && sessionQuery.hasNextPage, loadingMore: sessionQuery.isFetchingNextPage,
    loadMore: () => { void queryRef.current.fetchNextPage(); },
    error: sessionQuery.error ? t("sessions.listFailed") : null,
    select: selectSession, create: newSession, refresh: refreshSessions,
  }), [activeSessionId, navigationAvailable, status, sending, sessionQuery.data, sessionQuery.isFetching, sessionQuery.hasNextPage, sessionQuery.isFetchingNextPage, sessionQuery.error, t, selectSession, newSession, refreshSessions]);
  return {
    runtime, protocolVersion: client?.protocolVersion, status, error, reconnect, clear,
    mcpServers: endpoint?.mcp_servers ?? {}, modelHandle: endpoint?.model_handle ?? "",
    availableCommands: session.availableCommands, imageSupported, recordAttached,
    attachRecord, clearRecord, renderContext, permissions, answerPermission, sessions,
  };
}

/** One attach/consume/toggle rule for both protocol carriers and queued sends. */
function isRecordAttached(log: ChatMessage[], sessionId: string | null, viewKey: string, override: RecordOverride): boolean {
  if (override?.viewKey === viewKey) return override.attached;
  const last = log.findLast((message) => message.role === "user" && message.sentContext?.sessionId === sessionId && !message.deliveryFailed);
  return last?.sentContext === undefined || stableKey(last.sentContext.view) !== viewKey;
}

function parseEndpoint(data: DocumentType<typeof AgentChatEndpointMutation> | undefined, unsupported: string, invalid: string, explicit?: 1 | 2): AgentChatEndpoint {
  const payload = data?.agent_chat_endpoint;
  if (payload && ((payload.protocol_version !== 1 && payload.protocol_version !== 2) || (explicit !== undefined && explicit !== payload.protocol_version))) throw new EndpointError(unsupported);
  const parsed = v.safeParse(AgentChatEndpointSchema, payload);
  if (!parsed.success) throw new EndpointError(invalid);
  return parsed.output;
}
async function fetchSystemContext(
  renderPrompt: (variables: DocumentVariables<typeof RenderAgentPrompt>) => Promise<DocumentType<typeof RenderAgentPrompt> | undefined>,
  agentId: string, view: ReturnType<typeof agentChatViewInput>,
): Promise<string> {
  try {
    const data = await renderPrompt({ id: agentId, view });
    return data?.render_agent_prompt ?? "";
  } catch { return ""; }
}
function toMcpServers(servers: Record<string, McpServerConfig>): McpServer[] {
  return Object.entries(servers).map(([name, config]) => ({
    type: "http", name, url: config.url,
    headers: Object.entries(config.headers ?? {}).map(([key, value]) => ({ name: key, value })),
  }));
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
