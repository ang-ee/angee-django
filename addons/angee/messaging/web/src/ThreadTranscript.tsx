import { senderDisplayName } from "@angee/parties";
import {
  useAuthoredInfiniteQuery,
  type DocumentData,
  type DocumentVariables,
} from "@angee/refine";
import * as React from "react";
import { Button, ChatBubble, EmptyState, Glyph, LoadingPanel, MessagePartsView, ReactionBar, RelativeTime, SectionEyebrow, cn, reactionsFromGroups, textRoleVariants, type ChatBubbleRole } from "@angee/ui";
import { useVirtualizer } from "@tanstack/react-virtual";

import { useMessagingT } from "./i18n";
import { messagingReactionCopy } from "./reaction-copy";
import {
  ThreadTranscriptDocument,
  type ThreadTranscriptRow,
} from "./documents";

const MESSAGE_MODELS = ["messaging.Message", "messaging.Reaction"] as const;
// Newest-first head window size; "Load older" fetches keyset pages strictly
// before the oldest loaded row's (sent_at, created_at) cursor — constant work
// per fetch however deep the history, never a re-fetched growing window.
const PAGE_SIZE = 50;
// Estimated bubble height before measurement; the virtualizer remeasures each row.
const ESTIMATED_ROW_HEIGHT = 96;
// Placeholder cursor for the older field while the first page skips it.
const EPOCH = "1970-01-01T00:00:00Z";

type ThreadTranscriptData = DocumentData<typeof ThreadTranscriptDocument>;
type ThreadTranscriptVariables = DocumentVariables<typeof ThreadTranscriptDocument>;
type TranscriptPageVariables =
  Pick<ThreadTranscriptVariables, "head" | "beforeSentAt" | "beforeCreatedAt">;

/** Newest-first feed order mirroring the server page order (`sent_at desc,
 *  created_at desc`): Postgres puts NULLs first on a bare DESC, so a row
 *  without a send time sorts to the newest end here too. The id tiebreak is
 *  client-only (ids are opaque sqids) and matters just for stable rendering of
 *  exact timestamp ties. ISO-8601 strings in one timezone compare as strings. */
function compareNewestFirst(a: ThreadTranscriptRow, b: ThreadTranscriptRow): number {
  const aNull = a.sent_at == null;
  const bNull = b.sent_at == null;
  if (aNull !== bNull) return aNull ? -1 : 1;
  const aKey = a.sent_at ?? a.created_at;
  const bKey = b.sent_at ?? b.created_at;
  if (aKey !== bKey) return aKey < bKey ? 1 : -1;
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1;
  return a.id < b.id ? 1 : a.id > b.id ? -1 : 0;
}

function transcriptRows(data: ThreadTranscriptData): readonly ThreadTranscriptRow[] {
  return data.head_messages ?? data.older_messages ?? [];
}

function transcriptPageVariables(
  rows: readonly ThreadTranscriptRow[],
): TranscriptPageVariables | undefined {
  // "Load older" keyset page: before the oldest loaded row's (sent_at,
  // created_at) cursor, boundary-INCLUSIVE on created_at. Rows tying the anchor
  // refetch and the shared id-keyed infinite read dedupes the overlap, so a tie
  // at the page cut cannot be skipped (ids are opaque sqids, so they cannot be
  // the third cursor key server-side).
  if (rows.length < PAGE_SIZE) return undefined;
  for (let index = rows.length - 1; index >= 0; index -= 1) {
    const row = rows[index];
    if (row?.sent_at) {
      return {
        head: false,
        beforeSentAt: row.sent_at,
        beforeCreatedAt: row.created_at,
      };
    }
  }
  return undefined;
}

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
  // Remount per thread so scroll anchors and virtualizer measurements reset.
  return <TranscriptBody key={threadId} threadId={threadId} order={order} />;
}

function TranscriptBody({
  threadId,
  order = "conversation",
}: ThreadTranscriptProps): React.ReactElement {
  const t = useMessagingT();
  const variables = React.useMemo<ThreadTranscriptVariables>(
    () => ({
      threadId,
      limit: PAGE_SIZE,
      head: true,
      beforeSentAt: EPOCH,
      beforeCreatedAt: EPOCH,
    }),
    [threadId],
  );
  const transcript = useAuthoredInfiniteQuery(ThreadTranscriptDocument, variables, {
    enabled: Boolean(threadId),
    models: MESSAGE_MODELS,
    getRows: transcriptRows,
    getRowId: (row) => row.id,
    getPageParam: transcriptPageVariables,
  });

  // Render oldest-to-newest so the latest turn sits at the bottom.
  const messages = React.useMemo(
    () => [...transcript.rows].sort(compareNewestFirst).reverse(),
    [transcript.rows],
  );
  const total = transcript.pages[0]?.messages_aggregate?.aggregate?.count ?? messages.length;
  const hasOlder = transcript.hasMore && messages.length < total;
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
    if (!transcript.hasMore) return;
    const scroll = scrollRef.current;
    // Capture the pre-prepend distance from the bottom so the anchor effect can restore it.
    if (conversation && scroll !== null) prependAnchorRef.current = scroll.scrollHeight - scroll.scrollTop;
    transcript.fetchOlder();
  }

  if (transcript.fetching && transcript.rows.length === 0) {
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
            disabled={transcript.fetching || transcript.fetchingOlder}
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
  const author = senderDisplayName(message.sender, t("message.author"));
  const timestamp = message.sent_at ?? message.created_at;
  const reactions = reactionsFromGroups(
    message.reaction_groups,
    messagingReactionCopy(t),
  );

  const body = (
    <>
      <MessagePartsView parts={message.parts} resolveFileUrl={(file) => file.url} />
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
          <SectionEyebrow as="span" tone="warning">
            {t("transcript.noteLabel")}
          </SectionEyebrow>
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
