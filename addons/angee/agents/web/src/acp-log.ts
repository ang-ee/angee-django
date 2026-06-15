// The pure ACP→transcript reducer: folds `session/update` notifications into an
// immutable message log the chat runtime hands to assistant-ui. Kept free of React
// and assistant-ui so the streaming/coalescing/tool-upsert rules are unit-testable on
// their own (see `acp-log.test.ts`); `useAcpRuntime` owns the socket and the store.

import type { SessionNotification, ToolCallStatus } from "@zed-industries/agent-client-protocol";

/** One rendered part of an assistant message, in arrival order: streamed assistant
 *  text, the agent's reasoning/thinking, or a tool call with its input and result. */
export type ChatPart =
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | {
      kind: "tool";
      id: string;
      toolName: string;
      status: ToolCallStatus;
      input?: unknown;
      result?: unknown;
      isError?: boolean;
    };

/** A chat message held in the external store: a role and its ordered parts. */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  parts: ChatPart[];
}

/**
 * Fold one session update into `log`, returning a NEW array whose trailing assistant
 * message is a fresh object (new identity), so assistant-ui re-renders the stream. An
 * update that changes nothing returns `log` unchanged, avoiding a needless re-render.
 */
export function foldIntoLog(log: ChatMessage[], note: SessionNotification): ChatMessage[] {
  const last = log[log.length - 1];
  const isAssistant = last !== undefined && last.role === "assistant";
  const base: ChatMessage = isAssistant
    ? last
    : { id: `assistant-${log.length}`, role: "assistant", parts: [] };
  const next = applyUpdate(base, note);
  if (next === base) return log;
  return isAssistant ? [...log.slice(0, -1), next] : [...log, next];
}

/**
 * Apply one session update to `assistant`, returning a new message — text/reasoning chunks
 * coalesce into the trailing part, tool calls upsert by id, all immutably — or the same
 * reference when the update is not rendered.
 */
function applyUpdate(assistant: ChatMessage, note: SessionNotification): ChatMessage {
  const update = note.update;
  switch (update.sessionUpdate) {
    case "agent_message_chunk":
      return update.content.type === "text"
        ? { ...assistant, parts: appendText(assistant.parts, "text", update.content.text) }
        : assistant;
    case "agent_thought_chunk":
      return update.content.type === "text"
        ? { ...assistant, parts: appendText(assistant.parts, "reasoning", update.content.text) }
        : assistant;
    // ACP agents (e.g. Claude Code) emit `tool_call` more than once for the same id as
    // the tool input streams in, then `tool_call_update`s for status/result. Both upsert
    // by id so a re-sent call refreshes its part instead of appending a duplicate — which
    // assistant-ui rejects as a duplicate `toolCallId` key. `tool_call` creates the part
    // when new; `tool_call_update` only touches one that already exists.
    case "tool_call":
      return { ...assistant, parts: upsertToolPart(assistant.parts, update, true) };
    case "tool_call_update":
      return assistant.parts.some((part) => part.kind === "tool" && part.id === update.toolCallId)
        ? { ...assistant, parts: upsertToolPart(assistant.parts, update, false) }
        : assistant;
    default:
      return assistant;
  }
}

/** Append `text` to the trailing `kind` part (coalescing a run of chunks) or start one. */
function appendText(parts: ChatPart[], kind: "text" | "reasoning", text: string): ChatPart[] {
  const last = parts[parts.length - 1];
  if (last !== undefined && last.kind === kind) {
    return [...parts.slice(0, -1), { kind, text: last.text + text }];
  }
  return [...parts, { kind, text }];
}

type ToolPart = Extract<ChatPart, { kind: "tool" }>;

/** The fields read off an ACP `tool_call` / `tool_call_update` to build or refresh a part. */
interface ToolUpdateFields {
  toolCallId: string;
  title?: string | null;
  status?: ToolCallStatus | null;
  rawInput?: unknown;
  rawOutput?: unknown;
  content?: unknown;
}

/** Upsert the tool part carrying `update.toolCallId` into `parts`, immutably. When no such
 *  part exists it is appended only if `create` (a `tool_call`); a `tool_call_update` for an
 *  unknown id leaves `parts` untouched. */
function upsertToolPart(parts: ChatPart[], update: ToolUpdateFields, create: boolean): ChatPart[] {
  if (!parts.some((part) => part.kind === "tool" && part.id === update.toolCallId)) {
    return create ? [...parts, mergeToolPart(undefined, update)] : parts;
  }
  return parts.map((part) =>
    part.kind === "tool" && part.id === update.toolCallId ? mergeToolPart(part, update) : part,
  );
}

/** Merge an ACP tool update onto an existing tool part (or build a fresh one), keeping the
 *  prior value for every field the update omits. */
function mergeToolPart(existing: ToolPart | undefined, update: ToolUpdateFields): ToolPart {
  return {
    kind: "tool",
    id: update.toolCallId,
    toolName: typeof update.title === "string" ? update.title : existing?.toolName ?? "",
    status: update.status ?? existing?.status ?? "pending",
    input: update.rawInput ?? existing?.input,
    result: update.rawOutput ?? update.content ?? existing?.result,
    isError: update.status === "failed" ? true : existing?.isError,
  };
}
