import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  Avatar,
  Button,
  Glyph,
  MessageActions,
  MessageAttachmentChip,
  MessageComposer,
  MessageComposerHint,
  MessageDaySeparator,
  MessageFeed,
  MessageRow,
  ReactionBar,
  ReactionPicker,
  Tag,
  messageComposerInputClassName,
} from "@angee/ui";

const meta = {
  title: "Communication/Message",
  component: MessageFeed,
  parameters: { layout: "padded" },
} satisfies Meta<typeof MessageFeed>;

export default meta;

type Story = StoryObj;

const Surface = ({ children }: { children: React.ReactNode }) => (
  <div className="max-w-lg rounded-6 border border-border-subtle bg-sheet p-3">{children}</div>
);

const hour = 60 * 60 * 1000;
const now = Date.now();

/** A row's hover action set — reply / edit / delete. The row is a `group`, so `MessageActions`
 *  reveals on hover/focus. */
function RowActions() {
  return (
    <MessageActions>
      <Button size="iconSm" variant="ghost" aria-label="Reply">
        <Glyph name="quote" />
      </Button>
      <Button size="iconSm" variant="ghost" aria-label="Edit">
        <Glyph name="pencil" />
      </Button>
      <Button size="iconSm" variant="ghost" aria-label="Delete">
        <Glyph name="trash" />
      </Button>
    </MessageActions>
  );
}

/** The record chatter feed: a single left-aligned column interleaving a comment, an audit
 *  field-change (tracking table), and an inbound channel email (direction/channel affordance).
 *  Every actor renders in one column — direction is an inline tag, never an L/R split. */
export const Feed: Story = {
  render: () => (
    <Surface>
      <MessageFeed label="Comments">
        <MessageRow
          avatar={<Avatar size="sm" initials="AY" />}
          author="Alexis Yushin"
          timestamp={new Date(now - 2 * hour)}
          reactions={
            <ReactionBar
              label="Reactions"
              reactions={[
                { reaction: "👍", count: 3, active: true, title: "You, Sam and Robin" },
                { reaction: "🎉", count: 1 },
              ]}
              onToggle={() => undefined}
            />
          }
          actions={<RowActions />}
        >
          Shipping the feed primitive today — reviews all landed green.
        </MessageRow>

        <MessageRow
          avatar={<Avatar size="sm" initials="SB" />}
          author="Sam Brand"
          timestamp={new Date(now - 90 * 60 * 1000)}
          meta="· changed a field"
          tracking={
            <dl className="space-y-1 rounded-6 bg-inset p-2">
              <div className="grid grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-2 text-13">
                <dt className="truncate font-medium text-fg-muted">Stage</dt>
                <dd className="min-w-0 text-fg">
                  <span className="text-fg-muted">Draft</span>
                  <span aria-hidden="true"> → </span>
                  <span>In review</span>
                </dd>
              </div>
            </dl>
          }
          actions={<RowActions />}
        />

        <MessageDaySeparator>Today</MessageDaySeparator>

        <MessageRow
          avatar={<Avatar size="sm" initials="RC" />}
          author="Robin Chase"
          channel={
            <Tag tone="info" density="compact" shape="pill">
              Email · Inbound
            </Tag>
          }
          timestamp={new Date(now - 20 * 60 * 1000)}
          attachments={
            <a
              href="#"
              className="flex items-center gap-2 rounded-6 border border-border-subtle bg-sheet-2 px-2 py-1 text-13 text-fg hover:bg-inset"
            >
              <Glyph decorative name="attachment" className="shrink-0 text-fg-muted" />
              <span className="min-w-0 flex-1 truncate">contract-v3.pdf</span>
              <span className="shrink-0 text-fg-muted">240 KB</span>
            </a>
          }
          actions={<RowActions />}
        >
          Thanks — the updated contract is attached, signed on our side.
        </MessageRow>
      </MessageFeed>
    </Surface>
  ),
};

export const Row: Story = {
  render: () => (
    <Surface>
      <MessageFeed>
        <MessageRow
          avatar={<Avatar size="sm" initials="AY" />}
          author="Alexis Yushin"
          timestamp={new Date(now - 5 * 60 * 1000)}
          meta="· edited"
          actions={<RowActions />}
        >
          A single row: avatar, author, a relative timestamp, an edited marker, and a
          hover-revealed action set.
        </MessageRow>
      </MessageFeed>
    </Surface>
  ),
};

export const Reactions: Story = {
  render: () => (
    <div className="max-w-md space-y-3 p-3">
      {/* Each pill carries an accessible name ("👍 reaction, 3") so the glyph + count read
          correctly; `active` reflects the current user's own reaction. */}
      <ReactionBar
        label="Reactions"
        reactions={[
          { reaction: "👍", count: 12, active: true, title: "You and 11 others" },
          { reaction: "🎉", count: 4 },
          { reaction: "🚀", count: 1, title: "Sam" },
        ]}
        onToggle={() => undefined}
      />
      {/* Display-only (no `onToggle`) renders inert pills. */}
      <ReactionBar
        label="Reactions (read only)"
        reactions={[
          { reaction: "❤️", count: 8 },
          { reaction: "👀", count: 2 },
        ]}
      />
    </div>
  ),
};

export const ReactionAddPalette: Story = {
  render: () => (
    <div className="max-w-md space-y-3 p-3">
      {/* The count-less quick-add palette shares `ReactionBar`'s pill markup and
          "👍 reaction" accessible-name convention; `active` marks reactions the current
          user already applied so the palette doubles as a toggle. */}
      <ReactionPicker
        label="Add reaction"
        options={["👍", "❤️", "😂", "🎉"]}
        active={["👍"]}
        onToggle={() => undefined}
      />
    </div>
  ),
};

export const Composer: Story = {
  render: () => (
    <div className="max-w-md p-3">
      <MessageComposer
        input={<textarea className={messageComposerInputClassName} rows={3} placeholder="Write a comment…" />}
        hint={<MessageComposerHint />}
        actions={
          <Button size="sm" variant="primary">
            Send
          </Button>
        }
      />
    </div>
  ),
};

export const ComposerWithAttachments: Story = {
  render: () => (
    <div className="max-w-md p-3">
      {/* Presentation only: the consumer wires each chip to its attachment source; here both
          are static. */}
      <MessageComposer
        input={<textarea className={messageComposerInputClassName} rows={3} placeholder="Write a comment…" />}
        attachments={
          <>
            <MessageAttachmentChip
              icon={<Glyph name="file" className="h-3 w-3" />}
              onClick={() => undefined}
              remove={
                <button type="button" aria-label="Remove attachment" className="flex items-center text-fg-muted hover:text-fg">
                  <Glyph name="x" className="h-3 w-3" />
                </button>
              }
            >
              Current view
            </MessageAttachmentChip>
            <MessageAttachmentChip
              icon={<Glyph name="attachment" className="h-3 w-3" />}
              remove={
                <button type="button" aria-label="Remove attachment" className="flex items-center text-fg-muted hover:text-fg">
                  <Glyph name="x" className="h-3 w-3" />
                </button>
              }
            >
              screenshot.png
            </MessageAttachmentChip>
          </>
        }
        hint={<MessageComposerHint />}
        actions={
          <>
            <Button size="sm" variant="ghost" aria-label="Attach file">
              <Glyph name="attachment" className="h-4 w-4" />
            </Button>
            <Button size="sm" variant="primary">
              Send
            </Button>
          </>
        }
      />
    </div>
  ),
};
