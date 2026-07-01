import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Button,
  ChatBar,
  ChatBubble,
  ChatCommandEmpty,
  ChatCommandItem,
  ChatCommandList,
  ChatHeader,
  ChatHeaderAction,
  ChatTypingIndicator,
  ContextBlock,
  Glyph,
  MessageReasoningFrame,
  SessionRail,
  SessionRailItem,
  StatusDot,
  ToolFallback,
} from "@angee/ui";

const meta = {
  title: "Communication/Chat",
  component: ChatHeader,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ChatHeader>;

export default meta;

type Story = StoryObj;

const Surface = ({ children }: { children: React.ReactNode }) => (
  <div className="max-w-md overflow-hidden rounded-6 border border-border-subtle bg-sheet">
    {children}
  </div>
);

export const Header: Story = {
  render: () => (
    <Surface>
      <ChatHeader
        title="Demo Agent"
        subtitle="claude-sonnet-4-6"
        statusLabel="Ready"
        statusTone="success"
        actions={
          <>
            <ChatHeaderAction>⚙</ChatHeaderAction>
            <ChatHeaderAction>Clear</ChatHeaderAction>
            <ChatHeaderAction>Reconnect</ChatHeaderAction>
          </>
        }
      />
    </Surface>
  ),
};

export const Bar: Story = {
  render: () => (
    <Surface>
      {/* The dense single-row chat header: a leading status + agent/model label (the agents
          addon swaps a status dot + label for the live `AgentChooser`), and a trailing overflow
          (⋯) menu. Pure presentation — the consumer composes the chooser/menu. */}
      <ChatBar
        start={
          <span className="flex min-w-0 items-center gap-2">
            <StatusDot tone="success" label="Ready" />
            <span className="truncate text-13 font-medium text-fg">
              Demo Agent
              <span className="font-normal text-fg-muted"> · claude-sonnet-4-6</span>
            </span>
          </span>
        }
        end={
          <Button size="sm" variant="ghost" aria-label="Conversation options" className="size-7 px-0">
            <Glyph name="more-horizontal" className="h-4 w-4" />
          </Button>
        }
      />
    </Surface>
  ),
};

export const Sessions: Story = {
  render: () => (
    // The left rail of the full-page sessions view: a labelled `nav` with a "+ New" header
    // action over a `ul` of `SessionRailItem` rows. The active row carries `aria-current="page"`;
    // the consumer (the agents addon) renders each row through its router `Link` via `render`.
    <div className="flex h-80 w-60 overflow-hidden rounded-6 border border-border-subtle bg-sheet-2">
      <SessionRail
        label="Running agents"
        action={
          <Button size="sm" variant="ghost">
            <Glyph name="plus" className="h-4 w-4" />
            New agent
          </Button>
        }
      >
        <SessionRailItem
          active
          status={<StatusDot tone="success" label="Running" />}
          handle="claude-opus"
        >
          Scout
        </SessionRailItem>
        <SessionRailItem
          status={<StatusDot tone="success" label="Running" />}
          handle="claude-haiku"
        >
          Ranger
        </SessionRailItem>
      </SessionRail>
    </div>
  ),
};

export const Bubbles: Story = {
  render: () => (
    <div className="max-w-md space-y-3 p-3">
      <ChatBubble role="user">Summarize this note for me.</ChatBubble>
      <ChatBubble role="assistant">
        This note captures the Q3 planning decisions and three open follow-ups.
      </ChatBubble>
      <ChatBubble role="system">Context: viewing notes/note nt_8Hd2.</ChatBubble>
    </div>
  ),
};

export const ChannelTranscript: Story = {
  render: () => (
    // `ChatBubble` is not agent-only: a two-party channel/user conversation is the same
    // role-aligned transcript. Here an outbound reply from us trails right (`user`), the
    // inbound counterpart leads left (`assistant`), and an internal note is the distinct
    // `system` treatment. The channel-conversation transcript view composes this shape.
    <div className="max-w-md space-y-3 p-3">
      <ChatBubble role="assistant">
        Hi — my invoice still shows the old billing address. Can you fix it before the next run?
      </ChatBubble>
      <ChatBubble role="user">
        Done — I&apos;ve updated the address on your account and reissued the invoice.
      </ChatBubble>
      <ChatBubble role="system">Internal note: verified the change against the CRM before replying.</ChatBubble>
    </div>
  ),
};

export const TypingIndicator: Story = {
  render: () => (
    <div className="max-w-md p-3">
      {/* Presentation only: the agents addon gates this on assistant-ui's running state for the
          last started-but-empty assistant turn; here it renders standalone. */}
      <ChatBubble role="assistant">
        <ChatTypingIndicator />
      </ChatBubble>
    </div>
  ),
};

export const CommandPalette: Story = {
  render: () => (
    <div className="max-w-md p-3">
      {/* Presentation only: the agents addon binds these slots to assistant-ui's `/` trigger
          popover, which supplies role/highlight; here the highlighted row is shown statically. */}
      <ChatCommandList role="listbox" aria-label="Slash commands" className="relative">
        <ChatCommandItem label="/summarize" description="Summarize the note" data-highlighted="" />
        <ChatCommandItem label="/translate" description="Translate the note" />
        <ChatCommandItem label="/clear" description="Clear the conversation" />
      </ChatCommandList>
      <ChatCommandList role="listbox" aria-label="Slash commands" className="relative mt-3">
        <ChatCommandEmpty>No matching commands</ChatCommandEmpty>
      </ChatCommandList>
    </div>
  ),
};

export const ToolCalls: Story = {
  render: () => (
    <div className="max-w-md space-y-1 p-3">
      <ToolFallback toolName="read_note" input={{ sqid: "nt_8Hd2" }} />
      <ToolFallback
        toolName="read_note"
        input={{ sqid: "nt_8Hd2" }}
        result={{ title: "Q3 planning", word_count: 312 }}
      />
      <ToolFallback toolName="update_note" result="permission denied" isError />
    </div>
  ),
};

export const Reasoning: Story = {
  render: () => (
    <div className="max-w-md p-3">
      <MessageReasoningFrame>
        The user wants a summary. I should read the note first, then condense the decisions
        into bullets and surface the open follow-ups.
      </MessageReasoningFrame>
    </div>
  ),
};

export const Context: Story = {
  render: () => (
    <div className="max-w-md p-3">
      <ContextBlock label="context block (218 chars)">
        {"<system_context>\nThe user is viewing a record of notes/note.\n…\n</system_context>"}
      </ContextBlock>
    </div>
  ),
};
