/** Persisted-session transport for in-process agent runtimes. */

import * as React from "react";
import { useExternalStoreRuntime, type AppendMessage } from "@assistant-ui/react";
import type { SessionNotification } from "@agentclientprotocol/sdk";
import type { DocumentType } from "@angee/gql/console";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as v from "valibot";

import { messageOf } from "./acp-error";
import { convertMessage, foldIntoLog, type ChatMessage } from "./acp-log";
import { emptySession, foldIntoSession } from "./acp-session";
import {
  AgentSessionTurns,
  LatestAgentSession,
  PostAgentMessage,
  RenderAgentPrompt,
  StartAgentSession,
  type AgentChatView,
} from "./documents";
import type { AcpRuntime, AcpStatus } from "./useAcpRuntime";

const SESSION_MODELS = ["agents.AgentSession", "agents.AgentTurn"] as const;
const EMPTY_MCP_SERVERS = Object.freeze({});
const EMPTY_TURNS = [] as const;

const TextUpdateSchema = v.object({
  sessionUpdate: v.picklist(["agent_message_chunk", "agent_thought_chunk"]),
  content: v.object({ type: v.literal("text"), text: v.string() }),
});
const ToolStatusSchema = v.picklist(["pending", "in_progress", "completed", "failed"]);
const ToolCallSchema = v.object({
  sessionUpdate: v.literal("tool_call"),
  toolCallId: v.string(),
  title: v.string(),
  status: v.optional(ToolStatusSchema),
  rawInput: v.optional(v.unknown()),
  rawOutput: v.optional(v.unknown()),
});
const ToolCallUpdateSchema = v.object({
  sessionUpdate: v.literal("tool_call_update"),
  toolCallId: v.string(),
  title: v.optional(v.nullable(v.string())),
  status: v.optional(v.nullable(ToolStatusSchema)),
  rawInput: v.optional(v.unknown()),
  rawOutput: v.optional(v.unknown()),
});
const AvailableCommandSchema = v.object({
  name: v.string(),
  description: v.string(),
  input: v.optional(v.nullable(v.object({ hint: v.string() }))),
});
const StoredSessionUpdateSchema = v.variant("sessionUpdate", [
  TextUpdateSchema,
  ToolCallSchema,
  ToolCallUpdateSchema,
  v.object({
    sessionUpdate: v.literal("available_commands_update"),
    availableCommands: v.array(AvailableCommandSchema),
  }),
]);

export function useSessionRuntime(
  agentId: string,
  view: AgentChatView,
  initialSessionId?: string,
): AcpRuntime {
  const [startedSessionId, setStartedSessionId] = React.useState<string | null>(null);
  const [posting, setPosting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [recordAttached, setRecordAttached] = React.useState(true);
  const [clearedThrough, setClearedThrough] = React.useState(0);
  const startingRef = React.useRef<string | null>(null);
  const activeAgentRef = React.useRef(agentId);
  const turnMessagesRef = React.useRef(new Map<string, CachedTurnMessages>());

  const latest = useAuthoredQuery(LatestAgentSession, { agentId }, { models: SESSION_MODELS });
  const latestSession = latest.data?.agent_sessions[0];
  const reusableId = latestSession?.status === "CLOSED" ? undefined : latestSession?.id;
  const sessionId = initialSessionId ?? startedSessionId ?? reusableId;
  const turns = useAuthoredQuery(
    AgentSessionTurns,
    { sessionId: sessionId ?? "" },
    { enabled: sessionId !== undefined, models: SESSION_MODELS },
  );
  const queriedSession = turns.data?.agent_sessions[0];
  const displayedSession = queriedSession?.id === sessionId ? queriedSession : undefined;
  const displayedTurns = displayedSession ? turns.data?.agent_turns ?? EMPTY_TURNS : EMPTY_TURNS;
  const [startSession] = useAuthoredMutation(StartAgentSession, { invalidateModels: SESSION_MODELS });
  const [postMessage] = useAuthoredMutation(PostAgentMessage, { invalidateModels: SESSION_MODELS });
  const [renderPrompt] = useAuthoredMutation(RenderAgentPrompt);

  React.useEffect(() => {
    if (activeAgentRef.current === agentId) return;
    activeAgentRef.current = agentId;
    setStartedSessionId(null);
    setClearedThrough(0);
    setPosting(false);
    setError(null);
    startingRef.current = null;
    turnMessagesRef.current.clear();
  }, [agentId]);

  React.useEffect(() => {
    let active = true;
    if (initialSessionId !== undefined || latest.fetching || reusableId !== undefined) return;
    if (startingRef.current === agentId) return;
    startingRef.current = agentId;
    void startSession({ agent: agentId, context: view })
      .then((data) => {
        const id = data?.start_agent_session.id;
        if (active && id) setStartedSessionId(id);
      })
      .catch((caught) => {
        if (active) setError(messageOf(caught, "Failed to start the agent session."));
      });
    return () => {
      active = false;
    };
  }, [agentId, initialSessionId, latest.fetching, reusableId, startSession, view]);

  const allMessages = React.useMemo(
    () => transcriptMessages(sessionId ?? "", displayedTurns, turnMessagesRef.current),
    [sessionId, displayedTurns],
  );
  const messages = React.useMemo(() => allMessages.slice(clearedThrough), [allMessages, clearedThrough]);
  const latent = React.useMemo(() => {
    let state = emptySession;
    for (const turn of displayedTurns) {
      for (const raw of jsonArray(turn.updates)) {
        const note = storedNotification(sessionId ?? "", raw);
        if (note) state = foldIntoSession(state, note);
      }
    }
    return state;
  }, [sessionId, displayedTurns]);

  const onNew = React.useCallback(
    async (message: AppendMessage): Promise<void> => {
      const text = message.content.map((part) => part.type === "text" ? part.text : "").join("").trim();
      if (!sessionId || !text) return;
      setPosting(true);
      setError(null);
      try {
        await postMessage({ session: sessionId, text });
      } catch (caught) {
        setError(messageOf(caught, "The agent did not accept the message."));
      } finally {
        setPosting(false);
      }
    },
    [postMessage, sessionId],
  );
  const clear = React.useCallback(() => setClearedThrough(allMessages.length), [allMessages.length]);
  const onCancel = React.useCallback(async (): Promise<void> => setPosting(false), []);
  const attachRecord = React.useCallback((): void => setRecordAttached(true), []);
  const clearRecord = React.useCallback((): void => setRecordAttached(false), []);
  const reconnect = React.useCallback(() => {
    setClearedThrough(0);
    latest.refetch();
    turns.refetch();
  }, [latest.refetch, turns.refetch]);
  const renderContext = React.useCallback(async (): Promise<string> => {
    try {
      const data = await renderPrompt({ id: agentId, view });
      return data?.render_agent_prompt ?? "";
    } catch {
      return "";
    }
  }, [agentId, renderPrompt, view]);

  const runtime = useExternalStoreRuntime({
    isRunning: posting || displayedSession?.status === "RUNNING",
    messages,
    onNew,
    onCancel,
    convertMessage,
  });
  // Transport failures (start mutation, reads) disable the composer; a
  // domain error on the session (a failed turn's last_error) only shows the
  // banner — the session stays writable, and the next claimed turn clears
  // last_error via mark_running. Disabling on domain errors deadlocks the
  // session: the recovery message can never be sent. `|| null` because a
  // fresh session carries last_error="" and an empty banner is not an error.
  const transportError = error ?? latest.error?.message ?? turns.error?.message ?? null;
  const runtimeError = transportError ?? (displayedSession?.last_error || null);
  const status: AcpStatus = transportError
    ? "error"
    : sessionId && displayedSession
      ? displayedSession.status === "CLOSED" ? "closed" : "ready"
      : "connecting";
  return {
    runtime,
    status,
    error: runtimeError,
    reconnect,
    clear,
    mcpServers: EMPTY_MCP_SERVERS,
    modelHandle: displayedSession?.agent.model?.name ?? "",
    availableCommands: latent.availableCommands,
    imageSupported: false,
    recordAttachmentSupported: false,
    recordAttached,
    attachRecord,
    clearRecord,
    renderContext,
  };
}

function transcriptMessages(
  sessionId: string,
  turns: readonly StoredTurn[],
  cache: Map<string, CachedTurnMessages>,
): ChatMessage[] {
  let messages: ChatMessage[] = [];
  const currentIds = new Set(turns.map((turn) => turn.id));
  for (const id of cache.keys()) {
    if (!currentIds.has(id)) cache.delete(id);
  }
  for (const turn of turns) {
    const updates = jsonArray(turn.updates);
    const cached = cache.get(turn.id);
    let turnMessages = cached?.updatesLength === updates.length ? cached.messages : undefined;
    if (turnMessages === undefined) {
      turnMessages = [{ id: `user-${turn.id}`, role: "user", parts: [{ kind: "text", text: turn.prompt }] }];
      for (const raw of updates) {
        const note = storedNotification(sessionId, raw);
        if (note) turnMessages = foldIntoLog(turnMessages, note);
      }
      cache.set(turn.id, { updatesLength: updates.length, messages: turnMessages });
    }
    messages = [...messages, ...turnMessages];
  }
  return messages;
}

type StoredTurn = DocumentType<typeof AgentSessionTurns>["agent_turns"][number];

interface CachedTurnMessages {
  updatesLength: number;
  messages: ChatMessage[];
}

function jsonArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function storedNotification(sessionId: string, value: unknown): SessionNotification | null {
  const parsed = v.safeParse(StoredSessionUpdateSchema, value);
  return parsed.success ? { sessionId, update: parsed.output } : null;
}
