/** Persisted-session transport for in-process agent runtimes. */

import * as React from "react";
import { useExternalStoreRuntime, type AppendMessage, type ThreadMessageLike } from "@assistant-ui/react";
import type { SessionNotification } from "@agentclientprotocol/sdk";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";

import { messageOf } from "./acp-error";
import { foldIntoLog, type ChatMessage } from "./acp-log";
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

  const latest = useAuthoredQuery(LatestAgentSession, { agentId }, { models: SESSION_MODELS });
  const latestSession = latest.data?.agent_sessions[0];
  const reusableId = latestSession?.status === "CLOSED" ? undefined : latestSession?.id;
  const sessionId = initialSessionId ?? startedSessionId ?? reusableId;
  const turns = useAuthoredQuery(
    AgentSessionTurns,
    { sessionId: sessionId ?? "" },
    { enabled: sessionId !== undefined, models: SESSION_MODELS },
  );
  const [startSession] = useAuthoredMutation(StartAgentSession, { invalidateModels: SESSION_MODELS });
  const [postMessage] = useAuthoredMutation(PostAgentMessage, { invalidateModels: SESSION_MODELS });
  const [renderPrompt] = useAuthoredMutation(RenderAgentPrompt);

  React.useEffect(() => {
    setStartedSessionId(null);
    setClearedThrough(0);
    startingRef.current = null;
  }, [agentId]);

  React.useEffect(() => {
    if (initialSessionId !== undefined || latest.fetching || reusableId !== undefined) return;
    if (startingRef.current === agentId) return;
    startingRef.current = agentId;
    void startSession({ agent: agentId, context: view })
      .then((data) => {
        const id = data?.start_agent_session.id;
        if (id) setStartedSessionId(id);
      })
      .catch((caught) => setError(messageOf(caught, "Failed to start the agent session.")));
  }, [agentId, initialSessionId, latest.fetching, reusableId, startSession, view]);

  const allMessages = React.useMemo(
    () => transcriptMessages(sessionId ?? "", turns.data?.agent_turns ?? []),
    [sessionId, turns.data?.agent_turns],
  );
  const messages = React.useMemo(() => allMessages.slice(clearedThrough), [allMessages, clearedThrough]);
  const latent = React.useMemo(() => {
    let state = emptySession;
    for (const turn of turns.data?.agent_turns ?? []) {
      for (const raw of jsonArray(turn.updates)) {
        const note = storedNotification(sessionId ?? "", raw);
        if (note) state = foldIntoSession(state, note);
      }
    }
    return state;
  }, [sessionId, turns.data?.agent_turns]);

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
    isRunning: posting || latestSession?.status === "RUNNING",
    messages,
    onNew,
    onCancel: async () => setPosting(false),
    convertMessage,
  });
  const status: AcpStatus = error
    ? "error"
    : sessionId
      ? latestSession?.status === "CLOSED" ? "closed" : "ready"
      : "connecting";
  return {
    runtime,
    status,
    error: error ?? latest.error?.message ?? turns.error?.message ?? latestSession?.last_error ?? null,
    reconnect,
    clear,
    mcpServers: {},
    modelHandle: latestSession?.agent.model?.name ?? "",
    availableCommands: latent.availableCommands,
    imageSupported: false,
    recordAttached,
    attachRecord: () => setRecordAttached(true),
    clearRecord: () => setRecordAttached(false),
    renderContext,
  };
}

function transcriptMessages(sessionId: string, turns: readonly StoredTurn[]): ChatMessage[] {
  let messages: ChatMessage[] = [];
  for (const turn of turns) {
    messages = [...messages, { id: `user-${turn.id}`, role: "user", parts: [{ kind: "text", text: turn.prompt }] }];
    for (const raw of jsonArray(turn.updates)) {
      const note = storedNotification(sessionId, raw);
      if (note) messages = foldIntoLog(messages, note);
    }
  }
  return messages;
}

type StoredTurn = {
  id: string;
  prompt: string;
  updates: unknown;
};

function jsonArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function storedNotification(sessionId: string, value: unknown): SessionNotification | null {
  if (!isObject(value) || typeof value.sessionUpdate !== "string") return null;
  const kind = value.sessionUpdate;
  if (kind === "agent_message_chunk" || kind === "agent_thought_chunk") {
    if (!isObject(value.content) || value.content.type !== "text" || typeof value.content.text !== "string") return null;
  } else if (kind === "tool_call" || kind === "tool_call_update") {
    if (typeof value.toolCallId !== "string") return null;
    if (value.status !== undefined && !["pending", "in_progress", "completed", "failed"].includes(String(value.status))) return null;
  } else if (kind === "available_commands_update") {
    if (!Array.isArray(value.availableCommands)) return null;
  } else {
    return null;
  }
  return { sessionId, update: value } as SessionNotification;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function convertMessage(message: ChatMessage): ThreadMessageLike {
  const content = message.parts.map((part) => {
    if (part.kind === "text") return { type: "text" as const, text: part.text };
    if (part.kind === "reasoning") return { type: "reasoning" as const, text: part.text };
    if (part.kind === "image") return { type: "image" as const, image: part.image, filename: part.filename };
    return {
      type: "tool-call" as const,
      toolCallId: part.id,
      toolName: part.toolName,
      args: { status: part.status, input: part.input ?? null, result: part.result ?? null, isError: part.isError ?? false },
      argsText: "",
    };
  });
  return { id: message.id, role: message.role, content: content as ThreadMessageLike["content"] };
}
