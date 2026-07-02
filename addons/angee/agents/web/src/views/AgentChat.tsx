import * as React from "react";
import { Alert, ChatBar, ChatBubble, ChatHeaderAction, ChatTypingIndicator, ContextBlock, DialogBackdrop, DialogBody, DialogContent, DialogPortal, DialogRoot, DialogTitle, DropdownMenu, Glyph, MessageActions, MessageAttachmentChip, MessageComposer, MessageComposerHint, MessageReasoningFrame, StatusDot, ToolFallback, buttonVariants, cn, messageComposerInputClassName, statusTone, textRoleVariants } from "@angee/ui";
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
import { AgentChooser } from "./AgentChooser";
import { SlashCommandComposer } from "./slash-commands";
import type { AgentChatView, McpServerConfig, AgentRosterItem } from "../documents";

/**
 * Chat with a running agent over ACP. The session is minted per `agentId`; the browser
 * speaks ACP to the agent's routed WebSocket through the operator's central Caddy. The
 * surface is the `@angee/ui` chat primitives: a dense top bar (an agent chooser + a single
 * ⋯ overflow holding Settings/Reconnect/Clear), streamed markdown replies, reasoning frames,
 * and tool-call cards. `modelHandle` (when known) labels the agent's model in the bar + the
 * Settings dialog.
 *
 * When `agents` + `onSelectAgent` are passed, the bar's leading slot is an `AgentChooser`
 * (the session/thread switcher); selecting drives `onSelectAgent`, which the caller routes
 * into its keep-alive selection — never back into this component's `agentId` (that would
 * reset the runtime). Without them (e.g. the AgentsPage detail Chat tab) the bar shows a
 * static agent label. `fallbackName`/`modelHandle` label the chooser before the `AgentRoster`
 * loads or for a default agent not yet in the list.
 */
export function AgentChat({
  agentId,
  view,
  modelHandle,
  agents,
  selectedAgentId,
  onSelectAgent,
  fallbackName,
}: {
  agentId: string;
  view: AgentChatView;
  modelHandle?: string;
  agents?: readonly AgentRosterItem[];
  selectedAgentId?: string;
  onSelectAgent?: (id: string) => void;
  fallbackName?: string;
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
  const statusLabel = t(`chat.status.${status}`);
  const [settingsOpen, setSettingsOpen] = React.useState(false);

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="flex h-full min-h-[28rem] flex-col bg-sheet">
        <ChatBar
          start={
            <>
              {agents && onSelectAgent ? (
                <AgentChooser
                  agents={agents}
                  value={selectedAgentId ?? agentId}
                  onSelect={onSelectAgent}
                  status={status}
                  statusLabel={statusLabel}
                  fallbackName={fallbackName}
                  fallbackHandle={effectiveModelHandle}
                />
              ) : (
                <span className="flex min-w-0 items-center gap-2">
                  <StatusDot
                    tone={statusTone(status, { closed: "danger" })}
                    label={statusLabel}
                    className={status === "connecting" ? "motion-safe:animate-pulse" : undefined}
                  />
                  <span className="truncate text-13 font-medium text-fg">
                    {t("chat.title")}
                    {effectiveModelHandle ? (
                      <span className="font-normal text-fg-muted"> · {effectiveModelHandle}</span>
                    ) : null}
                  </span>
                </span>
              )}
              {/* The single connection live region — re-homes what the dropped status Tag
                  announced. Hidden keep-alive instances are display:none, so only the visible
                  agent announces its connecting → ready → error transitions. */}
              <span role="status" aria-live="polite" className="sr-only">
                {statusLabel}
              </span>
            </>
          }
          end={
            <DropdownMenu.Root>
              <DropdownMenu.Trigger
                aria-label={t("chat.conversationOptions")}
                className={buttonVariants({ variant: "ghost", size: "iconSm" })}
              >
                <Glyph name="more-horizontal" />
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Positioner side="bottom" align="end" sideOffset={4}>
                  <DropdownMenu.Content>
                    <DropdownMenu.Item onClick={() => setSettingsOpen(true)}>
                      <Glyph name="settings" />
                      <span className="flex-1 truncate">{t("chat.settings")}</span>
                    </DropdownMenu.Item>
                    <DropdownMenu.Item onClick={reconnect}>
                      <Glyph name="link" />
                      <span className="flex-1 truncate">{t("chat.reconnect")}</span>
                    </DropdownMenu.Item>
                    {/* Clear is destructive → last, after a separator. */}
                    <DropdownMenu.Separator />
                    <DropdownMenu.Item variant="danger" onClick={clear}>
                      <Glyph name="trash" />
                      <span className="flex-1 truncate">{t("chat.clear")}</span>
                    </DropdownMenu.Item>
                  </DropdownMenu.Content>
                </DropdownMenu.Positioner>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          }
        />
        <SettingsDialog
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          modelHandle={effectiveModelHandle}
          view={view}
          mcpServers={mcpServers}
          renderContext={renderContext}
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
                <p className={cn(textRoleVariants({ role: "meta" }), "leading-relaxed")}>{t("chat.empty")}</p>
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
            </ThreadPrimitive.Viewport>
            <ThreadPrimitive.ScrollToBottom
              aria-label={t("chat.scrollToBottom")}
              className={cn(
                buttonVariants({ variant: "secondary", size: "iconSm" }),
                "absolute bottom-3 right-3 z-10 rounded-full disabled:pointer-events-none disabled:opacity-0",
              )}
            >
              <Glyph name="arrow-down" className="h-4 w-4" />
            </ThreadPrimitive.ScrollToBottom>
          </div>
          <SlashCommandComposer commands={availableCommands}>
            <ComposerPrimitive.Root className="border-t border-border-subtle p-3">
              <MessageComposer
                input={
                  <ComposerPrimitive.Input
                    render={<textarea />}
                    className={messageComposerInputClassName}
                    rows={3}
                    placeholder={ready ? t("chat.placeholder") : t(`chat.status.${status}`)}
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
                hint={<MessageComposerHint />}
                actions={
                  <>
                    {imageSupported ? (
                      <ComposerPrimitive.AddAttachment
                        aria-label={t("chat.attach")}
                        className={buttonVariants({ variant: "ghost", size: "sm" })}
                      >
                        <Glyph name="attachment" className="h-4 w-4" />
                      </ComposerPrimitive.AddAttachment>
                    ) : null}
                    <ThreadPrimitive.If running={false}>
                      <ComposerPrimitive.Send
                        disabled={!ready}
                        className="text-13 text-accent disabled:text-fg-muted"
                      >
                        {t("chat.send")}
                      </ComposerPrimitive.Send>
                    </ThreadPrimitive.If>
                    <ThreadPrimitive.If running>
                      <ComposerPrimitive.Cancel className="text-13 text-danger-text">
                        {t("chat.stop")}
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
  <img src={image} alt={filename ?? ""} className="max-h-40 rounded-6" />
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
          <img src={previewUrl} alt={name} title={name} className="h-12 w-12 rounded-6 object-cover" />
          <AttachmentPrimitive.Remove
            aria-label={t("chat.removeAttachment")}
            className="absolute -right-1 -top-1 inline-flex size-4 items-center justify-center rounded-full border border-border-subtle bg-sheet-2 text-fg-muted shadow-xs hover:text-fg"
          >
            <Glyph name="x" className="h-3 w-3" />
          </AttachmentPrimitive.Remove>
        </div>
      ) : (
        <MessageAttachmentChip
          icon={<Glyph name="attachment" className="h-3 w-3" />}
          remove={
            <AttachmentPrimitive.Remove
              aria-label={t("chat.removeAttachment")}
              className="flex shrink-0 items-center text-fg-muted hover:text-fg"
            >
              <Glyph name="x" className="h-3 w-3" />
            </AttachmentPrimitive.Remove>
          }
        >
          <AttachmentPrimitive.Name />
        </MessageAttachmentChip>
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
    return <ChatHeaderAction onClick={attachRecord}>{t("chat.attachView")}</ChatHeaderAction>;
  }

  return (
    <>
      <MessageAttachmentChip
        icon={<Glyph name="file" className="h-3 w-3" />}
        onClick={() => setOpen(true)}
        remove={
          <button
            type="button"
            onClick={clearRecord}
            aria-label={t("chat.removeAttachment")}
            className="flex shrink-0 items-center text-fg-muted hover:text-fg"
          >
            <Glyph name="x" className="h-3 w-3" />
          </button>
        }
      >
        {t("chat.viewAttachment")}
      </MessageAttachmentChip>
      <DialogRoot open={open} onOpenChange={setOpen}>
        <DialogPortal>
          <DialogBackdrop />
          <DialogContent>
            <DialogTitle>{t("chat.context")}</DialogTitle>
            <DialogBody>
              <ContextBlock label={t("chat.inspectContext")}>{context || "—"}</ContextBlock>
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
      <MessageActions align="start">
        <ActionBarPrimitive.Root>
          <ActionBarPrimitive.Copy
            aria-label={t("chat.copy")}
            className={buttonVariants({ variant: "ghost", size: "sm" })}
          >
            <Glyph name="copy" className="h-3 w-3" />
          </ActionBarPrimitive.Copy>
        </ActionBarPrimitive.Root>
      </MessageActions>
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

/** The Settings dialog opened from the ⋯ overflow: the agent's model, MCP servers, the open
 *  view, and the rendered `<system_context>`. Controlled by the header; its body mounts on
 *  open, so the context fetch (and any per-instance work) only runs when the user opens it. */
function SettingsDialog({
  open,
  onOpenChange,
  modelHandle,
  view,
  mcpServers,
  renderContext,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  modelHandle?: string;
  view: AgentChatView;
  mcpServers: Record<string, McpServerConfig>;
  renderContext: () => Promise<string>;
}): React.ReactElement {
  const t = useAgentsT();
  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogBackdrop />
        <DialogContent>
          <DialogTitle>{t("chat.settings")}</DialogTitle>
          <DialogBody>
            <SessionInfo
              modelHandle={modelHandle}
              view={view}
              mcpServers={mcpServers}
              renderContext={renderContext}
            />
          </DialogBody>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  );
}

/** The settings dialog body: model + view + MCP servers + the rendered context (fetched
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
      <InfoRow label={t("chat.model")} value={modelHandle || "—"} />
      <InfoRow label={t("chat.viewLabel")} value={`${view.kind} · ${view.type}`} />
      <div>
        <div className="font-medium text-fg-muted">{t("chat.mcpServers")}</div>
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
      <ContextBlock label={t("chat.context")}>{context || "—"}</ContextBlock>
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
