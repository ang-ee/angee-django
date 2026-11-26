import { senderAvatarUrl, senderDisplayName } from "@angee/parties";
import { useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import {
  AbsoluteTime,
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
  NavLink,
  PaneToolbar,
  ReactionBar,
  StatusBadge,
  avatarInitials,
  formatDateTime,
  formatDayLabel,
  isTone,
  useEnumOptions,
  useReactionGroups,
  useResolvedWidget,
  useResourceRecordHrefLookup,
  type MessagePart,
  type PaneToolbarAction,
  type StatusDisplay,
  type Tone,
  type UnsavedChangesMove,
  type WidgetOption,
} from "@angee/ui";
import { useVirtualizer } from "@tanstack/react-virtual";

import { useMessagingT } from "./i18n";
import { MessageSourceCopies } from "./MessageSourceCopies";
import { CHANNEL_MODEL, MessagingSendableChannelIds, type MessageReplyData, type ThreadTranscriptRow } from "./documents";
import { MessageReplyQuote } from "./MessageReplyQuote";
import { messageFeedRows } from "./message-feed";
import { useThreadMessageFeed } from "./thread-message-feed";
import { ThreadReply } from "./ThreadReply";

const ESTIMATED_ROW_HEIGHT = 96;
const MESSAGE_MODEL = "messaging.Message";
const CHANNEL_MODELS = [CHANNEL_MODEL];

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
  /** Offer Reply on each email of a channel the viewer may send on (see {@link ThreadTranscriptReply});
   *  a host that passes nothing offers none. */
  reply?: ThreadTranscriptReply;
}

/**
 * A host's opt-in to answering email from its transcript: each email whose channel the server
 * lists as `sendable` for the viewer offers Reply, which docks a reply box under the
 * conversation. `leaves` names the route moves that abandon unsent text, in the host's route
 * vocabulary: those ask first, every other move keeps the box and its text.
 */
export interface ThreadTranscriptReply {
  leaves: (move: UnsavedChangesMove) => boolean;
}

/** How a message's status reads: its field's options, display and scoped tones, from the resource metadata. */
interface StatusPresentation {
  options: readonly WidgetOption[];
  display?: StatusDisplay;
  tone?: Record<string, Tone>;
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
  reply,
}: ThreadTranscriptProps): React.ReactElement {
  const [allCopies, setAllCopies] = React.useState(false);
  // The email the reply box answers, kept here, outside the remounted body, so the box and its
  // text survive every anchor and copy-mode move; it belongs to the thread it was opened on.
  const [target, setTarget] = React.useState<{ threadId: string; parent: ThreadTranscriptRow } | null>(null);
  const replying = reply && target?.threadId === threadId ? target.parent : null;
  // Raised after a send: the body shows its last row (an anchored transcript returns to the latest instead).
  const [revealLatest, setRevealLatest] = React.useState(0);
  // The box each Reply opens and controls.
  const replyBoxId = React.useId();
  // Where focus goes once the box closes: a row's Reply, else the list, once the rows of the
  // window it asks for have rendered (an anchored send returns to the latest, a new set of rows).
  const [focusAfter, setFocusAfter] = React.useState<PendingFocus | null>(null);
  const clearFocus = React.useCallback(() => setFocusAfter(null), []);
  const returnsToLatest = Boolean(anchor && onAnchorChange);
  const showLatest = () => {
    if (returnsToLatest) onAnchorChange?.("");
    else setRevealLatest((count) => count + 1);
  };
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
        reply={reply}
        onReply={(parent) => setTarget({ threadId, parent })}
        replyingTo={replying?.id}
        replyBoxId={replyBoxId}
        revealLatest={revealLatest}
        focusAfter={focusAfter}
        onFocused={clearFocus}
      />
      {/* Outside the body and its host's render window: a reply survives every remount of the rows. */}
      {reply && replying ? (
        <ThreadReply
          key={threadId}
          id={replyBoxId}
          threadId={threadId}
          parent={replying}
          leaves={reply.leaves}
          onShowLatest={showLatest}
          onSent={() => {
            setTarget(null);
            setFocusAfter({ id: null, anchor: returnsToLatest ? "" : anchor });
            showLatest();
          }}
          onClose={() => {
            setTarget(null);
            setFocusAfter({ id: replying.id, anchor });
          }}
        />
      ) : null}
    </div>
  );
}

/** Focus a closed reply box gives back: to the Reply of the email `id`, else the list, in the window `anchor`. */
interface PendingFocus {
  id: string | null;
  anchor: string;
}

interface TranscriptBodyProps extends ThreadTranscriptProps {
  allCopies: boolean;
  /** Focus to give once this body's rows render, when it is the window asked for. */
  focusAfter: PendingFocus | null;
  onFocused: () => void;
  /** Dock the reply box on this email. */
  onReply: (parent: ThreadTranscriptRow) => void;
  /** The email the reply box answers now, if any. */
  replyingTo?: string;
  /** The reply box's id, which each Reply controls. */
  replyBoxId: string;
  /** Show the last row when it rises. */
  revealLatest: number;
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
  reply,
  onReply,
  replyingTo,
  replyBoxId,
  revealLatest,
  focusAfter,
  onFocused,
}: TranscriptBodyProps): React.ReactElement {
  const t = useMessagingT();
  const transcript = useThreadMessageFeed(threadId, anchor, allCopies);
  // Render oldest-to-newest so the latest turn sits at the bottom.
  const messages = React.useMemo(
    () => messageFeedRows(transcript.data).reverse(),
    [transcript.data],
  );
  const status = useStatusPresentation();
  // The channels the loaded email rows are on: what the Reply gate asks the server about, once.
  const emailChannels = React.useMemo(() => [...new Set(messages.flatMap((message) =>
    message.message_type === "EMAIL" && message.channel ? [message.channel.id] : []))].sort(), [messages]);
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
    followOnAppend: conversation,
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

  // A rise of `revealLatest` (a reply was sent) brings the last row into view; a row that lands
  // later is followed by the virtualizer's own follow-on-append.
  const revealed = React.useRef(revealLatest);
  React.useLayoutEffect(() => {
    if (revealed.current === revealLatest || messages.length === 0) return;
    revealed.current = revealLatest;
    virtualizer.scrollToIndex(messages.length - 1, { align: "end" });
  }, [revealLatest, messages.length, virtualizer]);

  // A closed reply box's focus lands once the rows of the window it asked for are rendered.
  React.useLayoutEffect(() => {
    const list = listRef.current;
    if (focusAfter === null || focusAfter.anchor !== anchor || list === null) return;
    const reply = focusAfter.id ? list.querySelector<HTMLElement>(`[data-reply-for="${CSS.escape(focusAfter.id)}"]`) : null;
    (reply ?? list).focus();
    onFocused();
  }, [anchor, focusAfter, messages, onFocused]);

  function loadOlder(): void {
    if (!transcript.hasNextPage || transcript.isFetching) return;
    void transcript.fetchNextPage({ cancelRefetch: false });
  }
  const olderButton = hasOlder ? (
    <LoadMore direction="older" label={t("transcript.loadOlder")}
      disabled={transcript.isFetching || transcript.isFetchingNextPage} onClick={loadOlder} />
  ) : null;

  if (transcript.isFetching && messages.length === 0) {
    return <LoadingPanel loadingLabel={t("transcript.loading")} />;
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
          // Focus lands here when a reply box closes away from its row.
          tabIndex={-1}
          className="relative w-full p-3 outline-none"
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
                    status={status}
                    reply={reply && message.message_type === "EMAIL" && message.channel ? {
                      channel: message.channel.id, channels: emailChannels,
                      expanded: replyingTo === message.id, controls: replyBoxId, onReply: () => onReply(message),
                    } : undefined}
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

/** The status field's options, display and app-scoped tones, read once for every row. */
function useStatusPresentation(): StatusPresentation {
  const options = useEnumOptions(MESSAGE_MODEL, "status", { casing: "upper" });
  const field = useModelMetadata(MESSAGE_MODEL)?.fields.status;
  return React.useMemo(() => ({
    options,
    display: field?.statusDisplay ?? undefined,
    tone: field?.tones ? Object.fromEntries(Object.entries(field.tones)
      .filter((entry): entry is [string, Tone] => isTone(entry[1]))) : undefined,
  }), [field, options]);
}

/**
 * The delivery of a message written here and going out: its status, when it is due to go out
 * (a scheduled draft) or to be tried again (a queued retry), the attempts made, and for a failed
 * or uncertain one the way to its page, where those who may act read why and retry. A row shows it
 * only when written here (`is_locally_written`) and outgoing: synced mail and internal notes have none.
 */
function DeliveryMeta({ message, status, t, timezone }: {
  message: ThreadTranscriptRow;
  status: StatusPresentation;
  t: MessagingT;
  timezone?: string;
}): React.ReactElement {
  const recordHref = useResourceRecordHrefLookup();
  const value = message.status;
  const due = message.scheduled_at && (value === "DRAFT" || value === "QUEUED") ? message.scheduled_at : null;
  const href = value === "FAILED" || value === "UNCERTAIN" ? recordHref(MESSAGE_MODEL, message.id) : undefined;
  return <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-0.5">
    <StatusBadge value={value} options={status.options} tone={status.tone} display={status.display} />
    {/* The label precedes the time element: the namespace translator interpolates text, never an element. */}
    {due ? <span>{t(value === "DRAFT" ? "transcript.sendsAt" : "transcript.nextTry")}{" "}
      <AbsoluteTime value={due} timeZone={timezone} display="datetime" /></span> : null}
    {message.delivery_attempts > 0 && value !== "SENT"
      ? <span>{t("transcript.attempts", { count: message.delivery_attempts })}</span> : null}
    {href ? <NavLink href={href} variant="inline">{t("transcript.deliveryDetails")}</NavLink> : null}
  </span>;
}

interface ReplyButtonProps {
  /** The channel the email is on. */
  channel: string;
  /** Every channel the loaded email rows are on: one gate read for the whole window. */
  channels: readonly string[];
  /** The box is open on this email. */
  expanded: boolean;
  /** The box's id: Reply opens it, never toggles it. */
  controls: string;
  onReply: () => void;
}

/**
 * Reply on one email, offered when the server lists its channel as `sendable` for the viewer.
 * Every row asks the one read over the window's channels, so only a host that offers Reply reads
 * the gate, once per window; until it answers, no Reply shows.
 */
function ReplyButton({ channel, channels, expanded, controls, onReply, message, label }: ReplyButtonProps & {
  message: string;
  label: string;
}): React.ReactElement | null {
  const t = useMessagingT();
  const gate = useAuthoredQuery(MessagingSendableChannelIds, { ids: [...channels] }, { models: CHANNEL_MODELS });
  if (!gate.data?.channels.some((row) => row.id === channel)) return null;
  return (
    <Button size="sm" variant="ghost" data-reply-for={message} aria-expanded={expanded}
      aria-controls={expanded ? controls : undefined} aria-label={label} onClick={onReply}>
      <Glyph decorative name="reply" />
      {t("compose.reply")}
    </Button>
  );
}

interface TranscriptMessageProps {
  message: ThreadTranscriptRow;
  t: MessagingT;
  status: StatusPresentation;
  /** Reply on this email, on its `channel`, shown when the gate admits it, expanded while the box answers it. */
  reply?: ReplyButtonProps;
  timezone?: string;
  onPreviewFile?: ThreadTranscriptProps["onPreviewFile"];
  actions?: React.ReactNode;
  copies?: React.ReactNode;
  active?: boolean;
  onOpenReply?: ThreadTranscriptProps["onOpenReply"];
}

/** One transcript turn as the shared feed row: sender, direction chip, the absolute
 *  time, a message's delivery when it was written here, body, read-only reaction pills,
 *  Reply where the gate offers it, and the host's verbs on hover. */
function TranscriptMessage({ message, t, status, reply, timezone, onPreviewFile, onOpenReply, active, actions, copies }: TranscriptMessageProps): React.ReactElement {
  const recordHref = useResourceRecordHrefLookup();
  // Read the SDL's UPPERCASE `Direction` enum verbatim — one enum-casing convention
  // across the messaging web surface (see `message_type` reads in RecordChatterPane).
  const direction = message.direction;
  // An outgoing message whose sender the viewer cannot read went out from its channel.
  const author = senderDisplayName(message.sender,
    direction === "OUTBOUND" && message.channel?.display_name ? message.channel.display_name : t("message.author"));
  const timestamp = message.sent_at ?? message.created_at;
  const replyButton = reply ? (
    <ReplyButton {...reply} message={message.id}
      label={t("compose.replyTo", { author, time: formatDateTime(timestamp, { timeZone: timezone }) })} />
  ) : null;
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
      meta={message.is_locally_written && direction === "OUTBOUND"
        ? <DeliveryMeta message={message} status={status} t={t} timezone={timezone} /> : undefined}
      reactions={reactions.length > 0 ? <ReactionBar reactions={reactions} label={t("message.reactions")} /> : undefined}
      actions={replyButton || actions || copies ? <MessageActions className="flex-wrap">{replyButton}{copies}{actions}</MessageActions> : undefined}
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
