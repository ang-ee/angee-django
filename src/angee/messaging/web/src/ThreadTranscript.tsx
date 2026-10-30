import { senderAvatarUrl, senderDisplayName } from "@angee/parties";
import * as React from "react";
import {
  Avatar,
  Button,
  Chip,
  EmptyState,
  ErrorBanner,
  Glyph,
  LoadingPanel,
  MessageDaySeparator,
  MessageActions,
  MessagePartsView,
  MessageRow,
  PaneToolbar,
  ReactionBar,
  avatarInitials,
  formatDayLabel,
  useReactionGroups,
  useResolvedWidget,
  useResourceRecordHrefLookup,
  type MessagePart,
  type PaneToolbarAction,
} from "@angee/ui";
import { useVirtualizer } from "@tanstack/react-virtual";

import { useMessagingT } from "./i18n";
import { MessageSourceCopies } from "./MessageSourceCopies";
import type { MessageReplyData, ThreadTranscriptRow } from "./documents";
import { MessageReplyQuote } from "./MessageReplyQuote";
import { messageFeedRows } from "./message-feed";
import { useThreadMessageFeed } from "./thread-message-feed";

const ESTIMATED_ROW_HEIGHT = 96;

type MessagingT = ReturnType<typeof useMessagingT>;

/** How the transcript reads — decided by where it is placed, not hardcoded:
 *  - `conversation`: a chat/drawer surface — newest at the bottom, opened at its
 *    latest turn or explicit anchor, with position preserved across "Load older" prepends.
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
  /** Server resolves a bounded window around message:ID or date:YYYY-MM-DD. */
  anchor?: string;
  onAnchorChange?: (anchor: string) => void;
  /** Host controls join the transcript's one native pane toolbar. */
  toolbarActions?: readonly PaneToolbarAction[];
  timezone?: string;
  /** Wrap the visible window with an owner-provided annotation context. */
  renderWindow?: (
    messages: readonly ThreadTranscriptRow[],
    children: React.ReactElement,
  ) => React.ReactElement;
  renderMessageActions?: (message: ThreadTranscriptRow) => React.ReactNode;
  /** Open a quoted parent while preserving the host's navigation context. */
  onOpenReply?: (message: MessageReplyData) => void;
  /** Open an attachment in the host's preview. Without it, a chip opens the file
   *  URL itself (see `MessagePartsView`). */
  onPreviewFile?: (message: ThreadTranscriptRow, part: MessagePart) => void;
}

/**
 * The channel conversation transcript on a `messaging.Thread` detail: the thread's
 * messages as one left-aligned `MessageRow` feed — the direction (inbound, outbound,
 * internal note) is a chip on the row, never a left/right split. Unlike the bounded
 * record-chatter `MessageFeed`, a channel thread is unbounded, so the list is
 * virtualized with `@tanstack/react-virtual` (the locked long-list owner) and the
 * read grows a newest-first window on demand. Its controls — source copies, jump to
 * date — publish into the pane toolbar of whichever pane hosts it; the `order` prop
 * lets the placement decide the reading direction (see {@link TranscriptOrder}).
 */
export function ThreadTranscript({
  threadId,
  order = "conversation",
  anchor = "",
  onAnchorChange,
  renderMessageActions,
  renderWindow,
  timezone,
  onPreviewFile,
  onOpenReply,
  toolbarActions = [],
}: ThreadTranscriptProps): React.ReactElement {
  const [allCopies, setAllCopies] = React.useState(false);
  const t = useMessagingT();
  const jumpDate = anchor.startsWith("date:") ? anchor.slice(5).split("@")[0] : "";
  // Jump to date is the one date field; it stores a day, the anchor carries the zone.
  const DateEdit = useResolvedWidget("date")?.edit;
  const jumpField = React.useMemo(() => ({ name: "jump", label: t("transcript.jumpDate") }), [t]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneToolbar
        scope={onAnchorChange && DateEdit ? <DateEdit
          value={jumpDate || null} field={jumpField}
          onChange={(next) => onAnchorChange(typeof next === "string" && next ? `date:${next}${timezone ? `@${timezone}` : ""}` : "")}
        /> : undefined}
        actions={[
          ...(anchor && onAnchorChange ? [{ icon: "chevron-down", label: t("transcript.latest"), text: true, onClick: () => onAnchorChange("") }] : []),
          ...toolbarActions,
          { icon: "copy", label: t(allCopies ? "copies.collapse" : "copies.showAll"), pressed: allCopies, onClick: () => setAllCopies(!allCopies) },
        ]}
      />
      {/* Remount per thread, anchor and copy mode: each is a different row set, so the
          virtualizer's measurements and the scroll anchor start over. */}
      <TranscriptBody
        key={`${threadId}:${anchor}:${allCopies}`}
        threadId={threadId}
        allCopies={allCopies}
        order={order}
        anchor={anchor}
        onAnchorChange={onAnchorChange}
        renderMessageActions={renderMessageActions}
        renderWindow={renderWindow}
        timezone={timezone}
        onPreviewFile={onPreviewFile}
        onOpenReply={onOpenReply}
      />
    </div>
  );
}

function TranscriptBody({
  threadId,
  allCopies,
  order = "conversation",
  anchor = "",
  renderMessageActions,
  renderWindow,
  timezone,
  onPreviewFile,
  onOpenReply,
  onAnchorChange,
}: ThreadTranscriptProps & { allCopies: boolean }): React.ReactElement {
  const t = useMessagingT();
  const transcript = useThreadMessageFeed(threadId, anchor, allCopies);
  // Render oldest-to-newest so the latest turn sits at the bottom.
  const messages = React.useMemo(
    () => messageFeedRows(transcript.data).reverse(),
    [transcript.data],
  );
  const hasOlder = transcript.hasNextPage;
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
      list.getBoundingClientRect().top -
      scroll.getBoundingClientRect().top +
      scroll.scrollTop;
    setScrollMargin(margin);
  }, [hasOlder, messages.length]);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getItemKey: (index) => messages[index]!.id,
    anchorTo: conversation ? "end" : "start",
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    // Seed a viewport so rows window before the scroll element measures (also lets
    // the transcript render headless in tests).
    initialRect: { width: 640, height: 640 },
    scrollMargin,
    overscan: 8,
  });
  const totalSize = virtualizer.getTotalSize();

  // Conversation mode keeps the viewport anchored when "Load older" prepends
  // earlier turns. Open at the selected message, or at the latest turn by default.
  const scrolledThreadRef = React.useRef<string | null>(null);
  React.useLayoutEffect(() => () => {
    // The virtualizer cancels pending scroll reconciliation on effect teardown.
    // A StrictMode replay with cached rows must request the initial position again.
    scrolledThreadRef.current = null;
  }, []);
  React.useLayoutEffect(() => {
    const scroll = scrollRef.current;
    if (!conversation || scroll === null || messages.length === 0) return;
    if (scrolledThreadRef.current !== threadId) {
      const anchorIndex = anchor.startsWith("message:")
        ? messages.findIndex((message) => message.id === anchor.slice(8))
        : -1;
      if (anchorIndex >= 0)
        virtualizer.scrollToIndex(anchorIndex, { align: "center" });
      else if (!anchor)
        virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
      scrolledThreadRef.current = threadId;
    }
  }, [conversation, threadId, messages, totalSize, anchor, virtualizer]);

  function loadOlder(): void {
    if (!transcript.hasNextPage || transcript.isFetching) return;
    void transcript.fetchNextPage({ cancelRefetch: false });
  }
  const olderButton = hasOlder ? (
    <LoadMore direction="older" label={t("transcript.loadOlder")}
      disabled={transcript.isFetching || transcript.isFetchingNextPage} onClick={loadOlder} />
  ) : null;

  if (transcript.isFetching && messages.length === 0) {
    return <LoadingPanel message={t("transcript.loading")} />;
  }
  if (transcript.error) {
    return <ErrorBanner title={t("transcript.error")} description={t("transcript.emptyHint")} />;
  }
  if (messages.length === 0) {
    return (
      <EmptyState
        icon="comments"
        title={t("transcript.emptyTitle")}
        description={t("transcript.emptyHint")}
        actions={olderButton}
      />
    );
  }

  const virtualItems = virtualizer.getVirtualItems();
  const content = (
    <div className="flex h-full min-h-0 flex-col">
      {/* The "Load older" control sits OUTSIDE the scroll element so its height never
          offsets the virtualized list's coordinate space (the scrollMargin bug). */}
      {hasOlder ? (
        <div className="flex justify-center border-b border-border-subtle p-2">
          {olderButton}
        </div>
      ) : null}
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overscroll-contain overflow-y-auto"
      >
        <ul
          ref={listRef}
          aria-label={t("transcript.label")}
          className="relative w-full p-3"
          style={{ height: totalSize }}
        >
          {virtualItems.map((item) => {
            const message = messages[item.index];
            if (message === undefined) return null;
            const previous = messages[item.index - 1];
            const sentAt = message.sent_at ?? message.created_at;
            const day = formatDayLabel(sentAt, { timeZone: timezone });
            const previousDay = previous
              ? formatDayLabel(previous.sent_at ?? previous.created_at, { timeZone: timezone })
              : null;
            return (
              <li
                key={message.id}
                data-index={item.index}
                ref={virtualizer.measureElement}
                className="absolute left-0 top-0 w-full px-3 pb-4"
                style={{
                  transform: `translateY(${item.start - scrollMargin}px)`,
                }}
              >
                {day !== previousDay ? (
                  <MessageDaySeparator as="div" date={sentAt} timeZone={timezone} />
                ) : null}
                <ul>
                  <TranscriptMessage
                    message={message}
                    t={t}
                    timezone={timezone}
                    onPreviewFile={onPreviewFile}
                    active={anchor === `message:${message.id}`}
                    onOpenReply={onOpenReply ?? (onAnchorChange && message.parent?.thread?.id === threadId
                      ? (parent) => onAnchorChange(`message:${parent.id}`) : undefined)}
                    actions={renderMessageActions?.(message)}
                    copies={<MessageSourceCopies messageId={message.id} channelName={message.channel?.display_name}
                      renderActions={renderMessageActions} />}
                  />
                </ul>
              </li>
            );
          })}
        </ul>
      </div>
      {transcript.hasPreviousPage ? (
        <div className="flex justify-center border-t border-border-subtle p-2">
          <LoadMore direction="newer" label={t("transcript.loadNewer")} disabled={transcript.isFetching}
            onClick={() => void transcript.fetchPreviousPage({ cancelRefetch: false })} />
        </div>
      ) : null}
    </div>
  );
  return renderWindow
    ? renderWindow(
        virtualItems.flatMap((item) =>
          messages[item.index] ? [messages[item.index]!] : [],
        ),
        content,
      )
    : content;
}

/** The keyset "load more" control, one shape in both directions. */
function LoadMore({ direction, label, disabled, onClick }: {
  direction: "older" | "newer";
  label: string;
  disabled: boolean;
  onClick: () => void;
}): React.ReactElement {
  return (
    <Button type="button" variant="secondary" size="sm" disabled={disabled} onClick={onClick}>
      <Glyph name={direction === "older" ? "chevron-up" : "chevron-down"} />
      {label}
    </Button>
  );
}

interface TranscriptMessageProps {
  message: ThreadTranscriptRow;
  t: MessagingT;
  timezone?: string;
  onPreviewFile?: ThreadTranscriptProps["onPreviewFile"];
  actions?: React.ReactNode;
  copies?: React.ReactNode;
  active?: boolean;
  onOpenReply?: ThreadTranscriptProps["onOpenReply"];
}

/** One transcript turn as the shared feed row: sender, direction chip, the absolute
 *  time, body, read-only reaction pills, and the host's verbs on hover. */
function TranscriptMessage({ message, t, timezone, onPreviewFile, onOpenReply, active, actions, copies }: TranscriptMessageProps): React.ReactElement {
  const recordHref = useResourceRecordHrefLookup();
  // Read the SDL's UPPERCASE `Direction` enum verbatim — one enum-casing convention
  // across the messaging web surface (see `message_type` reads in RecordChatterPane).
  const direction = message.direction;
  const author = senderDisplayName(message.sender, t("message.author"));
  const timestamp = message.sent_at ?? message.created_at;
  const reactions = useReactionGroups(message.reaction_groups);
  const channel = direction === "INTERNAL"
    ? <Chip tone="warning">{t("transcript.noteLabel")}</Chip>
    : direction === "OUTBOUND"
      ? <Chip tone="info">{t("message.directionOutbound")}</Chip>
      : <Chip tone="neutral">{t("message.directionInbound")}</Chip>;
  return (
    <MessageRow
      aria-current={active ? true : undefined}
      className={active ? "selected rounded-6 p-2" : undefined}
      avatar={<Avatar size="sm" src={senderAvatarUrl(message.sender)} initials={avatarInitials(author)} alt={author} />}
      author={author}
      channel={channel}
      timestamp={timestamp}
      timeZone={timezone}
      reactions={reactions.length > 0 ? <ReactionBar reactions={reactions} label={t("message.reactions")} /> : undefined}
      actions={actions || copies ? <MessageActions className="flex-wrap">{copies}{actions}</MessageActions> : undefined}
    >
      {message.parent ? <MessageReplyQuote message={message.parent} className="mb-2"
        href={onOpenReply ? undefined : recordHref("messaging.Message", message.parent.id)}
        onOpen={onOpenReply ? () => onOpenReply(message.parent!) : undefined} /> : null}
      <MessagePartsView
        parts={message.parts}
        onPreviewFile={
          onPreviewFile ? (part) => onPreviewFile(message, part) : undefined
        }
      />
    </MessageRow>
  );
}
