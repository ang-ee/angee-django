import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Button,
  ChatBubble,
  ChatComposer,
  ChatComposerHint,
  ChatHeader,
  ChatHeaderAction,
  ContextBlock,
  MessageReasoningFrame,
  ToolFallback,
  chatComposerInputClassName,
} from "@angee/base";

const meta = {
  title: "Communication/Chat",
  component: ChatHeader,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ChatHeader>;

export default meta;

type Story = StoryObj;

const Surface = ({ children }: { children: React.ReactNode }) => (
  <div className="max-w-md overflow-hidden rounded-md border border-border-subtle bg-sheet">
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

export const Composer: Story = {
  render: () => (
    <div className="max-w-md p-3">
      <ChatComposer
        input={<textarea className={chatComposerInputClassName} rows={3} placeholder="Message the agent…" />}
        hint={<ChatComposerHint />}
        actions={
          <Button size="sm" variant="primary">
            Send
          </Button>
        }
      />
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
