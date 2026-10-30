// The pure ACP→transcript reducer: folds `session/update` notifications into an
// immutable message log the chat runtime hands to assistant-ui. The conversion boundary
// lives here too so both ACP transports share one exhaustive ChatPart mapping.

import type { ThreadMessageLike } from "@assistant-ui/react";
import type { JsonObject } from "@angee/ui";
import type { SessionNotification } from "@agentclientprotocol/sdk";
import { ContentBlock, SessionUpdate, StateUpdate, type ContentBlock as Content, type ToolCallContent, type UpdateSessionNotification } from "@agentclientprotocol/sdk/experimental/v2";
import type { AcpNotification } from "./acp-client";

/** One rendered part of a message, in arrival order: streamed assistant text, the agent's
 *  reasoning/thinking, a tool call with its input and result, or an inline image the user
 *  attached. v2 message replay includes authoritative user text and images. */
export type ChatPart = (
  | { kind: "text"; text: string }
  | { kind: "reasoning"; text: string }
  | { kind: "image"; image: string; filename?: string }
  | {
      kind: "tool";
      id: string;
      toolName: string;
      status: string;
      input?: unknown;
      result?: unknown;
      content?: ToolCallContent[] | null;
      isError?: boolean;
    }) & { messageId?: string };

/** A chat message held in the external store: a role and its ordered parts. */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  parts: ChatPart[];
  optimistic?: boolean;
  deliveryFailed?: boolean;
  /** Normalized view carried by this runtime's sent prompt; replay may omit it. */
  sentContext?: { sessionId: string; view: JsonObject };
  failed?: boolean;
  /** The latest foreground output was a full message containing readable text. */
  hasReadableMessage?: boolean;
  turnId?: string;
  status?: ThreadMessageLike["status"];
}

/**
 * Fold one session update into `log`, returning a NEW array with fresh objects for
 * affected messages, so assistant-ui re-renders the stream. An
 * update that changes nothing returns `log` unchanged, avoiding a needless re-render.
 */
export function foldIntoLog(log: ChatMessage[], notification: AcpNotification, failedMessage = "", stoppedMessage = ""): ChatMessage[] {
  if (notification.protocolVersion === 2) return foldV2(log, notification.params, failedMessage, stoppedMessage);
  const note = notification.params;
  // v1 history replay reports user prompts as chunks without message ids.
  if (note.update.sessionUpdate === "user_message_chunk") {
    const content = note.update.content;
    const last = log.at(-1);
    if (last?.optimistic) return log;
    const user: ChatMessage = last?.role === "user" ? last : { id: `user-${log.length}`, role: "user", parts: [] };
    const parts = appendContent(user.parts, content, false);
    if (parts === user.parts) return log;
    const next = { ...user, parts };
    return last === user ? [...log.slice(0, -1), next] : [...log, next];
  }
  const last = log[log.length - 1];
  const isAssistant = last !== undefined && last.role === "assistant";
  const base: ChatMessage = isAssistant
    ? last
    : { id: assistantMessageId(log, note), turnId: log.findLast((message) => message.role === "user")?.id, role: "assistant", parts: [] };
  const next = applyUpdate(base, note.update);
  if (next === base) return log;
  return isAssistant ? [...log.slice(0, -1), next] : [...log, next];
}

/** Derive the assistant id from its turn's user id, keeping re-folds stable. */
function assistantMessageId(log: ChatMessage[], note: SessionNotification): string {
  for (let index = log.length - 1; index >= 0; index -= 1) {
    const message = log[index];
    if (message?.role === "user") {
      return `assistant-${message.id.replace(/^user-/, "")}`;
    }
  }
  return `assistant-${note.sessionId}`;
}

/** Convert one chat message into assistant-ui's thread shape. */
export function convertMessage(message: ChatMessage): ThreadMessageLike {
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
            result: part.result ?? part.content ?? null,
            isError: part.isError ?? false,
          },
          argsText: "",
        };
      default: {
        const exhaustive: never = part;
        return exhaustive;
      }
    }
  });
  return { id: message.id, role: message.role, status: message.status,
    metadata: { custom: { acpDeliveryFailed: message.deliveryFailed === true, acpTurnFailed: message.failed === true } },
    content: content as ThreadMessageLike["content"] };
}

/**
 * Apply one session update to `assistant`, returning a new message — text/reasoning chunks
 * coalesce into the trailing part, tool calls upsert by id, all immutably — or the same
 * reference when the update is not rendered.
 */
function applyUpdate(assistant: ChatMessage, update: SessionNotification["update"]): ChatMessage {
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

/** Replace the optimistic id with the prompt's authoritative id, regardless of echo order. */
export function reconcileUserMessage(log: ChatMessage[], optimisticId: string, messageId: string): ChatMessage[] {
  if (log.some((message) => message.id === messageId)) {
    const local = log.find((message) => message.id === optimisticId);
    return log.filter((message) => message.id !== optimisticId).map((message) =>
      message.id === messageId && local?.sentContext ? { ...message, sentContext: local.sentContext } : message);
  }
  return log.map((message) => message.id === optimisticId ? { ...message, id: messageId } : message);
}

/** Settle the identified turn; queued user echoes never determine failure attribution. */
export function settleLog(log: ChatMessage[], stopReason: string, failedMessage: string, stoppedMessage: string, turnId: string): ChatMessage[] {
  const base = assistantForTurn(log, turnId);
  const index = log.findIndex((message) => message.id === base.id);
  if (base.status?.type === "incomplete" || base.status?.type === "complete") return log;
  const status: ThreadMessageLike["status"] = stopReason === "end_turn" ? { type: "complete", reason: "stop" }
    : { type: "incomplete", reason: stopReason === "cancelled" ? "cancelled" : "error" };
  const failed = stopReason === "_angee/failed";
  const text = failed ? base.hasReadableMessage ? "" : failedMessage : stopReason !== "end_turn" && stopReason !== "cancelled" ? stoppedMessage : "";
  const next = { ...base, status, failed, parts: text ? [...base.parts, { kind: "text" as const, text }] : base.parts };
  if (index >= 0) return log.map((message, position) => position === index ? next : message);
  return text ? insertAssistant(log, next) : log;
}

function assistantForTurn(log: ChatMessage[], turnId: string): ChatMessage {
  return log.find((message) => message.role === "assistant" && message.turnId === turnId)
    ?? { id: `assistant-${turnId}`, role: "assistant", turnId, parts: [] };
}

function insertAssistant(log: ChatMessage[], assistant: ChatMessage): ChatMessage[] {
  const userIndex = log.findIndex((message) => message.id === assistant.turnId);
  return userIndex < 0 ? [...log, assistant] : [...log.slice(0, userIndex + 1), assistant, ...log.slice(userIndex + 1)];
}

function foldV2(log: ChatMessage[], note: UpdateSessionNotification, failedMessage: string, stoppedMessage: string): ChatMessage[] {
  const update = note.update;
  // Ordered running/output/idle brackets own attribution, including on replay.
  // Queued user echoes leave the foreground entry in place until idle settles it.
  const foreground = log.find((message) => message.role === "assistant" && message.status?.type === "running");
  if (SessionUpdate.isStateUpdate(update)) {
    if (StateUpdate.isIdle(update)) return update.stopReason && foreground?.turnId
      ? settleLog(log, update.stopReason, failedMessage, stoppedMessage, foreground.turnId) : log;
    if (!StateUpdate.isRunning(update) && !StateUpdate.isRequiresAction(update)) return log;
    const oldest = log.find((message) => message.role === "user" && !message.optimistic && !message.deliveryFailed
      && !log.some((assistant) => assistant.turnId === message.id && (assistant.status?.type === "complete" || assistant.status?.type === "incomplete")));
    const base = foreground ?? (oldest ? assistantForTurn(log, oldest.id) : undefined);
    if (!base) return log;
    const next = { ...base, hasReadableMessage: false, status: { type: "running" as const } };
    return log.some((message) => message.id === base.id) ? log.map((message) => message.id === base.id ? next : message) : insertAssistant(log, next);
  }
  const thought = SessionUpdate.isAgentThought(update) || SessionUpdate.isAgentThoughtChunk(update);
  const user = SessionUpdate.isUserMessage(update) || SessionUpdate.isUserMessageChunk(update);
  if (SessionUpdate.isUserMessage(update) || SessionUpdate.isUserMessageChunk(update) || SessionUpdate.isAgentThought(update) || SessionUpdate.isAgentThoughtChunk(update) || SessionUpdate.isAgentMessage(update) || SessionUpdate.isAgentMessageChunk(update)) {
    const base = user ? log.find((message) => message.id === update.messageId) ?? { id: update.messageId, role: "user" as const, parts: [] }
      : foreground ?? log.find((message) => message.role === "assistant" && message.parts.some((part) => part.messageId === update.messageId));
    if (!base) return log;
    let parts = base.optimistic ? [] : base.parts;
    if (SessionUpdate.isUserMessageChunk(update) || SessionUpdate.isAgentMessageChunk(update) || SessionUpdate.isAgentThoughtChunk(update)) {
      parts = appendContent(parts, update.content, thought, user ? undefined : update.messageId);
    } else if (update.content !== undefined) {
      const replacement = (update.content ?? []).reduce<ChatPart[]>((acc, block) => appendContent(acc, block, thought, user ? undefined : update.messageId), []);
      const position = parts.findIndex((part) => part.messageId === update.messageId);
      const retained = user ? [] : parts.filter((part) => part.messageId !== update.messageId);
      const at = position < 0 ? retained.length : position;
      parts = [...retained.slice(0, at), ...replacement, ...retained.slice(at)];
    }
    const next = { ...base, optimistic: false, parts, ...(!user ? {
      hasReadableMessage: SessionUpdate.isAgentMessage(update) && (update.content ?? []).some((block) => ContentBlock.isText(block) && block.text.trim() !== ""),
    } : {}) };
    const index = log.findIndex((message) => message.id === base.id);
    // The echo places acceptance in the ordered stream; an earlier local draft
    // must not jump ahead of a prompt already accepted from another tab.
    if (user && base.optimistic) return [...log.filter((message) => message.id !== base.id), next];
    if (index >= 0) return log.map((message, position) => position === index ? next : message);
    if (!user) return insertAssistant(log, next);
    const assistant = log.findIndex((message) => message.turnId === update.messageId);
    return assistant < 0 ? [...log, next] : [...log.slice(0, assistant), next, ...log.slice(assistant)];
  }
  if (SessionUpdate.isToolCallUpdate(update) || SessionUpdate.isToolCallContentChunk(update)) {
    const base = foreground ?? log.find((message) => message.role === "assistant" && message.parts.some((part) => part.kind === "tool" && part.id === update.toolCallId));
    if (!base) return log;
    const tool = base.parts.find((part): part is ToolPart => part.kind === "tool" && part.id === update.toolCallId);
    const parts = SessionUpdate.isToolCallContentChunk(update)
      ? upsertToolPart(base.parts, { toolCallId: update.toolCallId, content: [...(tool?.content ?? []), update.content] }, true)
      : upsertToolPart(base.parts, update, true);
    const next = { ...base, parts, hasReadableMessage: false };
    return log.some((message) => message.id === base.id) ? log.map((message) => message.id === base.id ? next : message) : insertAssistant(log, next);
  }
  return log;
}

function appendContent(parts: ChatPart[], block: Content, thought: boolean, messageId?: string): ChatPart[] {
  if (ContentBlock.isText(block)) return appendText(parts, thought ? "reasoning" : "text", block.text, messageId);
  if (ContentBlock.isImage(block)) return [...parts, { kind: "image", image: `data:${block.mimeType};base64,${block.data}`, ...(messageId ? { messageId } : {}) }];
  return parts;
}

/** Append `text` to the trailing `kind` part (coalescing a run of chunks) or start one. */
function appendText(parts: ChatPart[], kind: "text" | "reasoning", text: string, messageId?: string): ChatPart[] {
  const last = parts[parts.length - 1];
  if (last !== undefined && last.kind === kind && last.messageId === messageId) {
    return [...parts.slice(0, -1), { ...last, text: last.text + text }];
  }
  return [...parts, { kind, text, ...(messageId ? { messageId } : {}) }];
}

type ToolPart = Extract<ChatPart, { kind: "tool" }>;

/** The fields read off an ACP `tool_call` / `tool_call_update` to build or refresh a part. */
interface ToolUpdateFields {
  toolCallId: string;
  title?: string | null;
  status?: string | null;
  rawInput?: unknown;
  rawOutput?: unknown;
  content?: ToolCallContent[] | null;
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
    toolName: update.title === undefined ? existing?.toolName ?? "" : update.title ?? "",
    status: update.status === undefined ? existing?.status ?? "pending" : update.status ?? "pending",
    input: update.rawInput === undefined ? existing?.input : update.rawInput,
    result: update.rawOutput === undefined ? existing?.result : update.rawOutput,
    content: update.content === undefined ? existing?.content : update.content,
    isError: update.status === undefined ? existing?.isError : update.status === "failed",
  };
}
