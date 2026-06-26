import * as React from "react";
import {
  Alert,
  ChatAttachmentChip,
  ChatBubble,
  ChatBubbleActions,
  ChatComposer,
  ChatComposerHint,
  ChatHeader,
  ChatHeaderAction,
  ChatTypingIndicator,
  ContextBlock,
  DialogBackdrop,
  DialogBody,
  DialogContent,
  DialogPortal,
  DialogRoot,
  DialogTitle,
  Glyph,
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
  ActionBarPrimitive,
  AssistantRuntimeProvider,
  AttachmentPrimitive,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAttachment,
  type ImageMessagePartComponent,
  type ReasoningMessagePartComponent,
  type TextMessagePartComponent,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { code } from "@streamdown/code";
import { Streamdown } from "streamdown";

import { useAcpRuntime } from "../useAcpRuntime";
import { useAgentsT } from "../i18n";
import { SlashCommandComposer } from "./slash-commands";
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
  const {
    runtime,
    status,
    error,
    reconnect,
    clear,
    mcpServers,
    availableCommands,
    imageSupported,
    recordAttached,
    attachRecord,
    clearRecord,
    renderContext,
  } = runtimeState;
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
          <div className="relative flex min-h-0 flex-1 flex-col">
            <ThreadPrimitive.Viewport autoScroll className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
              <ThreadPrimitive.Empty>
                <p className="text-13 leading-relaxed text-fg-muted">{t("agents.chat.empty")}</p>
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
            </ThreadPrimitive.Viewport>
            <ThreadPrimitive.ScrollToBottom
              aria-label={t("agents.chat.scrollToBottom")}
              className="absolute bottom-3 right-3 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full border border-border-subtle bg-sheet-2 text-fg-muted shadow-xs hover:bg-inset disabled:pointer-events-none disabled:opacity-0"
            >
              <Glyph name="arrow-down" className="h-4 w-4" />
            </ThreadPrimitive.ScrollToBottom>
          </div>
          <SlashCommandComposer commands={availableCommands}>
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
                attachments={
                  <>
                    <RecordAttachmentChip
                      attached={recordAttached}
                      attachRecord={attachRecord}
                      clearRecord={clearRecord}
                      renderContext={renderContext}
                    />
                    <ComposerPrimitive.Attachments>
                      {() => <ComposerImageAttachment />}
                    </ComposerPrimitive.Attachments>
                  </>
                }
                hint={<ChatComposerHint />}
                actions={
                  <>
                    {imageSupported ? (
                      <ComposerPrimitive.AddAttachment
                        aria-label={t("agents.chat.attach")}
                        className="inline-flex h-6 items-center rounded px-2 text-fg-muted hover:bg-inset disabled:text-fg-subtle"
                      >
                        <Glyph name="attachment" className="h-4 w-4" />
                      </ComposerPrimitive.AddAttachment>
                    ) : null}
                    <ThreadPrimitive.If running={false}>
                      <ComposerPrimitive.Send
                        disabled={!ready}
                        className="text-13 text-accent disabled:text-fg-muted"
                      >
                        {t("agents.chat.send")}
                      </ComposerPrimitive.Send>
                    </ThreadPrimitive.If>
                    <ThreadPrimitive.If running>
                      <ComposerPrimitive.Cancel className="text-13 text-danger-text">
                        {t("agents.chat.stop")}
                      </ComposerPrimitive.Cancel>
                    </ThreadPrimitive.If>
                  </>
                }
              />
            </ComposerPrimitive.Root>
          </SlashCommandComposer>
        </ThreadPrimitive.Root>
      </div>
    </AssistantRuntimeProvider>
  );
}

/** One user message: a right-aligned bubble of plain text and any inline images sent with it. */
function UserMessage(): React.ReactElement {
  return (
    <MessagePrimitive.Root className="mb-3">
      <ChatBubble role="user">
        <MessagePrimitive.Parts components={{ Text: PlainText, Image: UserImagePart }} />
      </ChatBubble>
    </MessagePrimitive.Root>
  );
}

const UserImagePart: ImageMessagePartComponent = ({ image, filename }) => (
  <img src={image} alt={filename ?? ""} className="max-h-40 rounded-md" />
);

/** A pending composer image attachment. `SimpleImageAttachmentAdapter` only fills the data-URL
 *  `content` on send, so the preview comes from the pending `file` itself (read via assistant-ui's
 *  `useAttachment`) turned into an object URL. When a file is present it renders a real thumbnail
 *  with an overlaid remove control; it falls back to the labelled chip for any future non-image
 *  (file) adapter. The `display:contents` root supplies the attachment context without adding a box. */
function ComposerImageAttachment(): React.ReactElement {
  const t = useAgentsT();
  const file = useAttachment((a) => a.file);
  const name = useAttachment((a) => a.name);
  const previewUrl = useObjectUrl(file);
  return (
    <AttachmentPrimitive.Root className="contents">
      {previewUrl !== null ? (
        <div className="relative">
          <img src={previewUrl} alt={name} title={name} className="h-12 w-12 rounded-md object-cover" />
          <AttachmentPrimitive.Remove
            aria-label={t("agents.chat.removeAttachment")}
            className="absolute -right-1 -top-1 inline-flex size-4 items-center justify-center rounded-full border border-border-subtle bg-sheet-2 text-fg-muted shadow-xs hover:text-fg"
          >
            <Glyph name="x" className="h-3 w-3" />
          </AttachmentPrimitive.Remove>
        </div>
      ) : (
        <ChatAttachmentChip
          icon={<Glyph name="attachment" className="h-3 w-3" />}
          remove={
            <AttachmentPrimitive.Remove
              aria-label={t("agents.chat.removeAttachment")}
              className="flex shrink-0 items-center text-fg-muted hover:text-fg"
            >
              <Glyph name="x" className="h-3 w-3" />
            </AttachmentPrimitive.Remove>
          }
        >
          <AttachmentPrimitive.Name />
        </ChatAttachmentChip>
      )}
    </AttachmentPrimitive.Root>
  );
}

/** Hold a live object URL for a `File`, revoking the previous one on change/unmount. Returns null
 *  when no file is present. Owns the `createObjectURL`/`revokeObjectURL` lifecycle so the preview
 *  never leaks a blob URL. */
function useObjectUrl(file: File | undefined): string | null {
  const [url, setUrl] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (file === undefined) {
      setUrl(null);
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [file]);
  return url;
}

/**
 * The current-view-as-record chip. This is deliberately NOT a native assistant-ui attachment:
 * composer attachments are cleared on every send and freeze their content at create-time, which
 * would break the record's default-present/persistent and fresh-at-send requirements, and would
 * route a second context-assembly path through `onNew`. Instead its presence is runtime state
 * (`recordAttached`) that gates the single leading context block in `buildPromptBlocks`.
 *
 * When attached it shows a chip whose label opens an inspector (the freshly rendered
 * `<system_context>`) and whose remove control clears the record; when cleared it shows a button
 * to re-attach the current view.
 */
function RecordAttachmentChip({
  attached,
  attachRecord,
  clearRecord,
  renderContext,
}: {
  attached: boolean;
  attachRecord: () => void;
  clearRecord: () => void;
  renderContext: () => Promise<string>;
}): React.ReactElement {
  const t = useAgentsT();
  const [open, setOpen] = React.useState(false);
  const context = useRenderedContext(renderContext, open);

  if (!attached) {
    return <ChatHeaderAction onClick={attachRecord}>{t("agents.chat.attachView")}</ChatHeaderAction>;
  }

  return (
    <>
      <ChatAttachmentChip
        icon={<Glyph name="file" className="h-3 w-3" />}
        onClick={() => setOpen(true)}
        remove={
          <button
            type="button"
            onClick={clearRecord}
            aria-label={t("agents.chat.removeAttachment")}
            className="flex shrink-0 items-center text-fg-muted hover:text-fg"
          >
            <Glyph name="x" className="h-3 w-3" />
          </button>
        }
      >
        {t("agents.chat.viewAttachment")}
      </ChatAttachmentChip>
      <DialogRoot open={open} onOpenChange={setOpen}>
        <DialogPortal>
          <DialogBackdrop />
          <DialogContent>
            <DialogTitle>{t("agents.chat.context")}</DialogTitle>
            <DialogBody>
              <ContextBlock label={t("agents.chat.inspectContext")}>{context || "—"}</ContextBlock>
            </DialogBody>
          </DialogContent>
        </DialogPortal>
      </DialogRoot>
    </>
  );
}

/** One assistant message: streamed markdown text, reasoning frames, and tool-call cards,
 *  with a hover/focus action row to copy the reply text. */
function AssistantMessage(): React.ReactElement {
  const t = useAgentsT();
  return (
    <MessagePrimitive.Root className="group mb-3">
      <ChatBubble role="assistant">
        <MessagePrimitive.Parts
          components={{ Text: AssistantText, Reasoning: ReasoningPart, tools: { Fallback: ToolPart } }}
        />
        {/* Pre-token "thinking" dots: only on the last assistant turn that has started but has no
            content yet, and only while the thread is running. `hasContent` flips true the moment
            the first text/reasoning/tool part streams in, which removes the indicator. */}
        <MessagePrimitive.If last hasContent={false}>
          <ThreadPrimitive.If running>
            <ChatTypingIndicator />
          </ThreadPrimitive.If>
        </MessagePrimitive.If>
      </ChatBubble>
      <ChatBubbleActions role="assistant">
        <ActionBarPrimitive.Root>
          <ActionBarPrimitive.Copy
            aria-label={t("agents.chat.copy")}
            className="inline-flex h-6 items-center gap-1 rounded px-2 text-2xs text-fg-muted hover:bg-inset"
          >
            <Glyph name="copy" className="h-3 w-3" />
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
      </ChatBubbleActions>
    </MessagePrimitive.Root>
  );
}

const PlainText: TextMessagePartComponent = ({ text }) => (
  <span className="whitespace-pre-wrap">{text}</span>
);

const AssistantText: TextMessagePartComponent = ({ text }) => (
  <Streamdown
    parseIncompleteMarkdown
    plugins={{ code }}
    lineNumbers={false}
    className="text-13 leading-relaxed"
  >
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
  const context = useRenderedContext(renderContext);

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

/** Fetch the freshly rendered `<system_context>` into state. Re-fetches when `renderContext`
 *  changes; `enabled` gates it (e.g. only while an inspector dialog is open). Shared by the
 *  session-info popover and the view-record inspector so the fetch lives in one place. */
function useRenderedContext(renderContext: () => Promise<string>, enabled = true): string {
  const [context, setContext] = React.useState("");
  React.useEffect(() => {
    if (!enabled) return;
    let active = true;
    void renderContext().then((text) => {
      if (active) setContext(text);
    });
    return () => {
      active = false;
    };
  }, [enabled, renderContext]);
  return context;
}
