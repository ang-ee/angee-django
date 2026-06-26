import * as React from "react";
import {
  Alert,
  ChatBubble,
  ChatComposer,
  ChatComposerHint,
  ChatHeader,
  ChatHeaderAction,
  ContextBlock,
  MessageReasoningFrame,
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
  ToolFallback,
  chatComposerInputClassName,
  statusTone as resolveStatusTone,
} from "@angee/ui";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type ReasoningMessagePartComponent,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { Streamdown } from "streamdown";

import { useAcpRuntime } from "../useAcpRuntime";
import { useAgentsT } from "../i18n";
import type { AgentChatView, McpServerConfig } from "../documents";

/**
 * Chat with a running agent over ACP. The session is minted per `agentId`; the browser
 * speaks ACP to the agent's routed WebSocket through the operator's central Caddy. The
 * surface is the `@angee/ui` chat primitives: a status header with a settings cog
 * (model + MCP servers + the rendered `<system_context>`) and Clear/Reconnect, streamed
 * markdown replies, reasoning frames, and tool-call cards. `modelHandle` (when known)
 * labels the agent's model in the header subtitle + settings.
 */
export function AgentChat({
  agentId,
  view,
  modelHandle,
}: {
  agentId: string;
  view: AgentChatView;
  modelHandle?: string;
}): React.ReactElement {
  const t = useAgentsT();
  const runtimeState = useAcpRuntime(agentId, view);
  const { runtime, status, error, reconnect, clear, mcpServers, renderContext } = runtimeState;
  const effectiveModelHandle = runtimeState.modelHandle || modelHandle;
  const ready = status === "ready";

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex h-full min-h-[28rem] flex-col bg-sheet">
        <ChatHeader
          title={t("agents.chat.title")}
          subtitle={effectiveModelHandle}
          statusLabel={t(`agents.chat.status.${status}`)}
          statusTone={resolveStatusTone(status)}
          actions={
            <>
              <SessionInfoPopover
                modelHandle={effectiveModelHandle}
                view={view}
                mcpServers={mcpServers}
                renderContext={renderContext}
              />
              <ChatHeaderAction onClick={clear}>{t("agents.chat.clear")}</ChatHeaderAction>
              <ChatHeaderAction onClick={reconnect}>{t("agents.chat.reconnect")}</ChatHeaderAction>
            </>
          }
        />
        {error !== null ? (
          <Alert tone="danger" className="m-3">
            {error}
          </Alert>
        ) : null}
        <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
          <ThreadPrimitive.Viewport autoScroll className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
            <ThreadPrimitive.Empty>
              <p className="text-13 leading-relaxed text-fg-muted">{t("agents.chat.empty")}</p>
            </ThreadPrimitive.Empty>
            <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
          </ThreadPrimitive.Viewport>
          <ComposerPrimitive.Root className="border-t border-border-subtle p-3">
            <ChatComposer
              input={
                <ComposerPrimitive.Input
                  className={chatComposerInputClassName}
                  rows={3}
                  placeholder={ready ? t("agents.chat.placeholder") : t(`agents.chat.status.${status}`)}
                  disabled={!ready}
                />
              }
              hint={<ChatComposerHint />}
              actions={
                <ComposerPrimitive.Send
                  disabled={!ready}
                  className="text-13 text-accent disabled:text-fg-muted"
                >
                  {t("agents.chat.send")}
                </ComposerPrimitive.Send>
              }
            />
          </ComposerPrimitive.Root>
        </ThreadPrimitive.Root>
      </div>
    </AssistantRuntimeProvider>
  );
}

/** One user message: a right-aligned bubble of plain text. */
function UserMessage(): React.ReactElement {
  return (
    <MessagePrimitive.Root className="mb-3">
      <ChatBubble role="user">
        <MessagePrimitive.Parts components={{ Text: PlainText }} />
      </ChatBubble>
    </MessagePrimitive.Root>
  );
}

/** One assistant message: streamed markdown text, reasoning frames, and tool-call cards. */
function AssistantMessage(): React.ReactElement {
  return (
    <MessagePrimitive.Root className="mb-3">
      <ChatBubble role="assistant">
        <MessagePrimitive.Parts
          components={{ Text: AssistantText, Reasoning: ReasoningPart, tools: { Fallback: ToolPart } }}
        />
      </ChatBubble>
    </MessagePrimitive.Root>
  );
}

const PlainText: TextMessagePartComponent = ({ text }) => (
  <span className="whitespace-pre-wrap">{text}</span>
);

const AssistantText: TextMessagePartComponent = ({ text }) => (
  <Streamdown parseIncompleteMarkdown className="text-13 leading-relaxed">
    {text}
  </Streamdown>
);

const ReasoningPart: ReasoningMessagePartComponent = ({ text }) => (
  <MessageReasoningFrame>{text}</MessageReasoningFrame>
);

const ToolPart: ToolCallMessagePartComponent = ({ toolName, args }) => {
  // The ACP status/input/result/isError ride the tool part's `args` bag (set in
  // `convertMessage`); `ToolFallback` renders them.
  const bag = args as { status?: string; input?: unknown; result?: unknown; isError?: boolean };
  return (
    <ToolFallback
      toolName={toolName}
      status={bag.status}
      input={bag.input}
      result={bag.result}
      isError={bag.isError === true}
    />
  );
};

/** The header settings cog: a popover with the agent's model, MCP servers, the open view,
 *  and the rendered `<system_context>` for it. */
function SessionInfoPopover({
  modelHandle,
  view,
  mcpServers,
  renderContext,
}: {
  modelHandle?: string;
  view: AgentChatView;
  mcpServers: Record<string, McpServerConfig>;
  renderContext: () => Promise<string>;
}): React.ReactElement {
  const t = useAgentsT();
  return (
    <PopoverRoot>
      <PopoverTrigger
        aria-label={t("agents.chat.settings")}
        className="inline-flex h-6 items-center rounded px-2 text-2xs text-fg-muted hover:bg-inset"
      >
        ⚙
      </PopoverTrigger>
      <PopoverPortal>
        <PopoverPositioner sideOffset={4} align="end">
          <PopoverContent className="w-72 p-3">
            <SessionInfo
              modelHandle={modelHandle}
              view={view}
              mcpServers={mcpServers}
              renderContext={renderContext}
            />
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}

/** The settings popover body: model + view + MCP servers + the rendered context (fetched
 *  on open). */
function SessionInfo({
  modelHandle,
  view,
  mcpServers,
  renderContext,
}: {
  modelHandle?: string;
  view: AgentChatView;
  mcpServers: Record<string, McpServerConfig>;
  renderContext: () => Promise<string>;
}): React.ReactElement {
  const t = useAgentsT();
  const [context, setContext] = React.useState("");
  React.useEffect(() => {
    let active = true;
    void renderContext().then((text) => {
      if (active) setContext(text);
    });
    return () => {
      active = false;
    };
  }, [renderContext]);

  const servers = Object.keys(mcpServers);
  return (
    <div className="space-y-2 text-2xs">
      <InfoRow label={t("agents.chat.model")} value={modelHandle || "—"} />
      <InfoRow label={t("agents.chat.viewLabel")} value={`${view.kind} · ${view.type}`} />
      <div>
        <div className="font-medium text-fg-muted">{t("agents.chat.mcpServers")}</div>
        {servers.length === 0 ? (
          <div className="text-fg-subtle">—</div>
        ) : (
          servers.map((name) => (
            <div key={name} className="truncate text-fg">
              {name}
            </div>
          ))
        )}
      </div>
      <ContextBlock label={t("agents.chat.context")}>{context || "—"}</ContextBlock>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }): React.ReactElement {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-fg-muted">{label}</span>
      <span className="truncate text-fg">{value}</span>
    </div>
  );
}
