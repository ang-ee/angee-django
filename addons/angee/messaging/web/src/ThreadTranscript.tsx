import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, ChatBubble, EmptyState, Glyph, LoadingPanel, MessageAttachmentChip, ReactionBar, RelativeTime, cn, textRoleVariants, type ChatBubbleRole, type Reaction } from "@angee/ui";
import { formatSize } from "@angee/ui/preview/index";
import { useVirtualizer } from "@tanstack/react-virtual";

import { useMessagingT } from "./i18n";
import { ThreadTranscriptDocument, type ThreadTranscriptRow } from "./documents";

const MESSAGE_MODELS = ["messaging.Message", "messaging.Reaction"] as const;
// Newest-first window size; "Load older" grows the limit by one page. The read is
// static and server-paginated — no live runtime, no streaming.
const PAGE_SIZE = 50;
// Estimated bubble height before measurement; the virtualizer remeasures each row.
const ESTIMATED_ROW_HEIGHT = 96;

type MessagingT = ReturnType<typeof useMessagingT>;

/** How the transcript reads — decided by where it is placed, not hardcoded:
 *  - `conversation`: a chat/drawer surface — newest at the bottom, scrolled to the
 *    latest turn once on open, scroll position anchored across "Load older" prepends.
 *  - `history`: a mail-like aside — oldest at the top, read top-down, no auto-scroll.
 *  Both render the same oldest→newest order; the mode only decides the scroll behavior. */
export type TranscriptOrder = "conversation" | "history";

export interface ThreadTranscriptProps {
  /** The thread's public id — the message window filters `thread._eq threadId`. */
  threadId: string;
  /** Reading order for the placement (see {@link TranscriptOrder}); defaults to
   *  `conversation`. A mail-like aside placement passes `history`. This is the
   *  extension point a widget/placement descriptor sets — `ThreadsPage` composes it
   *  directly, so no descriptor-contract field is needed to reach it. */
  order?: TranscriptOrder;
}

/**
 * The channel conversation transcript on a `messaging.Thread` detail: the thread's
 * messages as a role-aligned `ChatBubble` transcript — inbound counterparts lead
 * left, our outbound turns trail right, and internal notes get a distinct centered
 * treatment. Unlike the bounded record-chatter `MessageFeed`, a channel thread is
 * unbounded, so the list is virtualized with `@tanstack/react-virtual` (the locked
 * long-list owner) and the read grows a newest-first window on demand. The `order`
 * prop lets the placement decide the reading direction (see {@link TranscriptOrder}).
 */
export function ThreadTranscript({
  threadId,
  order = "conversation",
}: ThreadTranscriptProps): React.ReactElement {
  const t = useMessagingT();
  const [limit, setLimit] = React.useState(PAGE_SIZE);
  const variables = React.useMemo(() => ({ threadId, limit }), [threadId, limit]);
  const transcript = useAuthoredQuery(ThreadTranscriptDocument, variables, {
    enabled: Boolean(threadId),
    models: MESSAGE_MODELS,
  });

  // The window arrives newest-first (stable pagination against an appending
  // thread); render it oldest-to-newest so the latest turn sits at the bottom.
  const windowRows = transcript.data?.messages;
  const messages = React.useMemo(() => [...(windowRows ?? [])].reverse(), [windowRows]);
  const total = transcript.data?.messages_aggregate.aggregate?.count ?? messages.length;
  const hasOlder = messages.length < total;
  const conversation = order === "conversation";

  const scrollRef = React.useRef<HTMLDivElement>(null);
  const listRef = React.useRef<HTMLUListElement>(null);
  // The virtualized <ul> does not begin at the scroll element's content top (its own
  // padding sits above the first row), so the virtualizer must know that offset or
  // every item's `start` is wrong — masked only by overscan. Measure the list's offset
  // inside the scroll element and fold it back out of each row's translateY.
  const [scrollMargin, setScrollMargin] = React.useState(0);
  React.useLayoutEffect(() => {
    const list = listRef.current;
    const scroll = scrollRef.current;
    if (list === null || scroll === null) return;
    const margin =
      list.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop;
    setScrollMargin(margin);
  }, [hasOlder, messages.length]);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    // Seed a viewport so rows window before the scroll element measures (also lets
    // the transcript render headless in tests).
    initialRect: { width: 640, height: 640 },
    scrollMargin,
    overscan: 8,
  });
  const totalSize = virtualizer.getTotalSize();

  // Conversation mode reads newest-at-bottom: land on the latest turn once per thread
  // on open, and keep the viewport anchored when "Load older" prepends earlier turns.
  const scrolledThreadRef = React.useRef<string | null>(null);
  const prependAnchorRef = React.useRef<number | null>(null);
  React.useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (!conversation || scroll === null || messages.length === 0) return;
    if (prependAnchorRef.current !== null) {
      // Restore the distance from the bottom captured before the prepend, so the row
      // the reader was on stays put while older turns fill in above it.
      scroll.scrollTop = scroll.scrollHeight - prependAnchorRef.current;
      prependAnchorRef.current = null;
      return;
    }
    if (scrolledThreadRef.current !== threadId) {
      scroll.scrollTop = scroll.scrollHeight;
      scrolledThreadRef.current = threadId;
    }
  }, [conversation, threadId, messages.length, totalSize]);

  function loadOlder(): void {
    const scroll = scrollRef.current;
    // Capture the pre-prepend distance from the bottom so the anchor effect can restore it.
    if (conversation && scroll !== null) prependAnchorRef.current = scroll.scrollHeight - scroll.scrollTop;
    setLimit((current) => current + PAGE_SIZE);
  }

  if (transcript.fetching && windowRows === undefined) {
    return <LoadingPanel message={t("transcript.loading")} />;
  }
  if (transcript.error) {
    return (
      <EmptyState
        icon="comments"
        title={t("transcript.error")}
        description={t("transcript.emptyHint")}
        className="min-h-48 p-4"
      />
    );
  }
  if (messages.length === 0) {
    return (
      <EmptyState
        icon="comments"
        title={t("transcript.emptyTitle")}
        description={t("transcript.emptyHint")}
        className="min-h-48 p-4"
      />
    );
  }

  const virtualItems = virtualizer.getVirtualItems();
  return (
    <div className="rounded-6 border border-border-subtle bg-sheet">
      {/* The "Load older" control sits OUTSIDE the scroll element so its height never
          offsets the virtualized list's coordinate space (the scrollMargin bug). */}
      {hasOlder ? (
        <div className="flex justify-center border-b border-border-subtle p-2">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            disabled={transcript.fetching}
            onClick={loadOlder}
          >
            <Glyph name="chevron-up" />
            {t("transcript.loadOlder")}
          </Button>
        </div>
      ) : null}
      <div ref={scrollRef} className="overflow-y-auto" style={{ maxHeight: "calc(100vh - 16rem)" }}>
        <ul
          ref={listRef}
          aria-label={t("transcript.label")}
          className="relative w-full p-3"
          style={{ height: totalSize }}
        >
          {virtualItems.map((item) => {
            const message = messages[item.index];
            if (message === undefined) return null;
            return (
              <li
                key={message.id}
                data-index={item.index}
                ref={virtualizer.measureElement}
                className="absolute left-0 top-0 w-full px-3 pb-4"
                style={{ transform: `translateY(${item.start - scrollMargin}px)` }}
              >
                <TranscriptMessage message={message} t={t} />
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

interface TranscriptMessageProps {
  message: ThreadTranscriptRow;
  t: MessagingT;
}

/** One transcript turn: an internal note as a distinct centered card, otherwise a
 *  role-aligned `ChatBubble` (outbound trails right, inbound leads left) with the
 *  sender/time header, body, attachment chips, and read-only reaction pills. */
function TranscriptMessage({ message, t }: TranscriptMessageProps): React.ReactElement {
  // Read the SDL's UPPERCASE `Direction` enum verbatim — one enum-casing convention
  // across the messaging web surface (see `message_type` reads in RecordChatterPane).
  const direction = message.direction;
  const author = message.sender?.display_name || message.sender?.value || t("message.author");
  const text = transcriptText(message);
  const timestamp = message.sent_at ?? message.created_at;
  const attachments = message.parts
    .map((part) => part.file)
    .filter((file): file is NonNullable<typeof file> => file !== null);
  const reactions: Reaction[] = message.reaction_groups.map((group) => ({
    reaction: group.reaction,
    count: group.count,
    active: group.self_reacted,
    title: reactionTitle(group),
  }));

  const body = (
    <>
      {text ? <div className="whitespace-pre-wrap leading-relaxed">{text}</div> : null}
      {attachments.length > 0 ? (
        <div className="mt-2 space-y-1">
          {attachments.map((file) => (
            <a key={file.id} href={file.url} download={file.filename} className="block max-w-full">
              <MessageAttachmentChip
                icon={<Glyph decorative name="attachment" />}
                remove={<span className="shrink-0 text-2xs text-fg-subtle">{formatSize(file.size_bytes)}</span>}
              >
                {file.title || file.filename}
              </MessageAttachmentChip>
            </a>
          ))}
        </div>
      ) : null}
      {reactions.length > 0 ? (
        <div className="mt-2">
          <ReactionBar reactions={reactions} label={t("message.reactions")} />
        </div>
      ) : null}
    </>
  );

  // Internal notes are not sent to the counterpart, so they get a distinct
  // full-width note treatment instead of a left/right conversation bubble.
  if (direction === "INTERNAL") {
    return (
      <div className="rounded-6 border border-dashed border-border-subtle bg-surface-inset px-3 py-2 text-13 text-fg">
        <div className="mb-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-2xs font-semibold uppercase tracking-wide text-warning-text">
            {t("transcript.noteLabel")}
          </span>
          <span className="text-13 font-medium text-fg">{author}</span>
          {timestamp ? (
            <RelativeTime value={timestamp} className={textRoleVariants({ role: "caption" })} />
          ) : null}
        </div>
        {body}
      </div>
    );
  }

  const role: ChatBubbleRole = direction === "OUTBOUND" ? "user" : "assistant";
  const mine = role === "user";
  return (
    <div className={cn("flex flex-col", mine ? "items-end" : "items-start")}>
      <div className="flex items-baseline gap-2 px-1 pb-1">
        <span className="text-2xs font-medium text-fg">{author}</span>
        {timestamp ? (
          <RelativeTime value={timestamp} className={textRoleVariants({ role: "caption" })} />
        ) : null}
      </div>
      <ChatBubble role={role} className="w-full">
        {body}
      </ChatBubble>
    </div>
  );
}

function transcriptText(message: Pick<ThreadTranscriptRow, "parts" | "preview">): string {
  const part = message.parts.find((item) => item.fragment?.text);
  return part?.fragment?.text ?? message.preview ?? "";
}

function reactionTitle(group: ThreadTranscriptRow["reaction_groups"][number]): string {
  const names = group.handles
    .map((handle) => handle.display_name || handle.value)
    .filter((value) => value.trim() !== "");
  if (names.length === 0) return `${group.reaction} ${group.count.toLocaleString()}`;
  return `${group.reaction} by ${names.join(", ")}`;
}
