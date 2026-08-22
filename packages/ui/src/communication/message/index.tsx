// Presentational message primitives — the multi-actor record-chatter *feed* and the
// cross-surface message atoms shared with the role-aligned chat transcript
// (`communication/chat`). No transport coupling: a consumer wires the GraphQL/urql
// data, the mutations, and any streaming runtime around these. Tokens + `tone` follow
// the base design system; copy routes through `useUiT`.

import { useId, useState, type HTMLAttributes, type ReactElement, type ReactNode } from "react";

import { Glyph } from "../../chrome/Glyph";
import { RelativeTime } from "../../fragments/RelativeTime";
import { useUiT } from "../../i18n";
import { cn } from "../../lib/cn";
import { formatSize, isImageMime, normaliseMime } from "../../preview/model";
import { Chip, type ChipTone } from "../../ui/chip";
import { Kbd } from "../../ui/kbd";
import { textRoleVariants } from "../../ui/text";
import { Textarea, textareaVariants } from "../../ui/textarea";

// ---------------------------------------------------------------------------
// Feed
// ---------------------------------------------------------------------------

export interface MessageFeedProps extends HTMLAttributes<HTMLUListElement> {
  /** Accessible name for the feed list (e.g. "Comments"). */
  label?: string;
  /** Marks the list busy while messages load (skeleton rows render as children). */
  busy?: boolean;
}

/** A single-column, chronological message feed: a semantic `ul` of `MessageRow`
 *  items (with optional `MessageDaySeparator` rows between day groups). Unlike the
 *  role-aligned `ChatBubble` transcript, every actor renders in one left-aligned
 *  column — the direction/channel is an inline affordance on the row, never an L/R
 *  split.
 *
 *  Deliberately non-virtualized: a record chatter thread is bounded (≤50 messages),
 *  so a plain list is correct and cheaper than a virtualizer. An unbounded surface
 *  (a channel conversation transcript) must wire `useVirtualizer` instead — the
 *  `@tanstack/react-virtual` owner — rather than reaching for this feed. */
export function MessageFeed({ label, busy, className, children, ...props }: MessageFeedProps): ReactElement {
  return (
    <ul
      aria-label={label}
      aria-busy={busy || undefined}
      className={cn("flex flex-col gap-4", className)}
      {...props}
    >
      {children}
    </ul>
  );
}

export interface MessageDaySeparatorProps extends HTMLAttributes<HTMLLIElement> {
  /** The day label (e.g. "Today", "12 May"). The consumer owns the date formatting. */
  children: ReactNode;
}

/** An optional day-group separator row: a centered, muted label dividing the feed into
 *  day buckets. Pure presentation — the consumer decides where the day boundaries fall
 *  and formats the label. */
export function MessageDaySeparator({ children, className, ...props }: MessageDaySeparatorProps): ReactElement {
  return (
    <li className={cn("flex items-center gap-2 py-1", className)} {...props}>
      <span className="h-px flex-1 bg-border-subtle" aria-hidden />
      <span className={cn(textRoleVariants({ role: "caption" }), "shrink-0")}>{children}</span>
      <span className="h-px flex-1 bg-border-subtle" aria-hidden />
    </li>
  );
}

// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------

export interface MessageRowProps extends Omit<HTMLAttributes<HTMLLIElement>, "title"> {
  /** Leading avatar slot (e.g. an `Avatar` for the author). */
  avatar?: ReactNode;
  /** The author name / handle. */
  author?: ReactNode;
  /** The message timestamp — rendered as a `RelativeTime` in the row header. */
  timestamp?: Date | string | null;
  /** Inline meta beside the timestamp (e.g. an "edited" marker). */
  meta?: ReactNode;
  /** A direction/channel affordance (e.g. a "Email · Inbound" tag) for interleaved
   *  transport messages, rendered in the header row. */
  channel?: ReactNode;
  /** A tracking-values table slot (a field-change `dl`/table for audit messages). */
  tracking?: ReactNode;
  /** An attachments slot (download chips / thumbnails). */
  attachments?: ReactNode;
  /** A reactions slot — compose `ReactionBar`. */
  reactions?: ReactNode;
  /** A hover-revealed action row — compose `MessageActions`. */
  actions?: ReactNode;
  /** The message body / rendered parts. */
  children?: ReactNode;
}

/** One feed message: a leading avatar column and a content column carrying the author,
 *  an optional channel/direction affordance, a `RelativeTime`, the body, and the
 *  tracking / attachments / reactions / actions slots. The row is a `group`, so a
 *  `MessageActions` slot reveals on hover/focus. Pure presentation — every slot is
 *  composed by the consumer. */
export function MessageRow({
  avatar,
  author,
  timestamp,
  meta,
  channel,
  tracking,
  attachments,
  reactions,
  actions,
  className,
  children,
  ...props
}: MessageRowProps): ReactElement {
  return (
    <li className={cn("group flex gap-2.5", className)} {...props}>
      {avatar ? <div className="shrink-0 pt-0.5">{avatar}</div> : null}
      <div className="min-w-0 flex-1">
        {author || channel || timestamp || meta ? (
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            {author ? <span className="text-13 font-medium text-fg">{author}</span> : null}
            {channel}
            {timestamp ? (
              <RelativeTime value={timestamp} className={textRoleVariants({ role: "caption" })} />
            ) : null}
            {meta ? <span className={textRoleVariants({ role: "caption" })}>{meta}</span> : null}
          </div>
        ) : null}
        {children ? <div className="mt-0.5 whitespace-pre-wrap text-13 leading-relaxed text-fg">{children}</div> : null}
        {tracking ? <div className="mt-2">{tracking}</div> : null}
        {attachments ? <div className="mt-2 space-y-1">{attachments}</div> : null}
        {reactions ? <div className="mt-2">{reactions}</div> : null}
        {actions}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Parts
// ---------------------------------------------------------------------------

export interface MessagePartMime {
  mime_type?: string | null;
  label?: string | null;
}

export interface MessagePartFile {
  id?: string | null;
  filename?: string | null;
  title?: string | null;
  url?: string | null;
  size_bytes?: number | null;
  mime_type?: MessagePartMime | null;
}

export interface MessagePartFragment {
  text?: string | null;
}

export interface MessagePart {
  id?: string | null;
  role?: string | null;
  fragment?: MessagePartFragment | null;
  disposition?: string | null;
  cid?: string | null;
  file?: MessagePartFile | null;
}

export interface MessagePartsViewProps extends Omit<HTMLAttributes<HTMLDivElement>, "children"> {
  /** Ordered message parts as selected by the consumer's transport. */
  parts: readonly MessagePart[];
  /** Resolves a viewable/downloadable URL for a file part. */
  resolveFileUrl: (file: MessagePartFile) => string | null | undefined;
}

/** Renders a message's ordered MIME/JMAP part list: title/body/quote/signature text
 *  plus inline images and attachment chips. Presentation only — consumers pass the
 *  already ordered parts and a file-URL resolver; no transport or GraphQL detail lives
 *  here. */
export function MessagePartsView({
  parts,
  resolveFileUrl,
  className,
  ...props
}: MessagePartsViewProps): ReactElement | null {
  if (!parts.some(hasRenderableMessagePart)) return null;
  return (
    <div className={cn("space-y-2 whitespace-normal text-13 leading-relaxed text-current", className)} {...props}>
      {parts.map((part, index) =>
        hasRenderableMessagePart(part) ? (
          <MessagePartItem
            key={messagePartKey(part, index)}
            part={part}
            resolveFileUrl={resolveFileUrl}
          />
        ) : null,
      )}
    </div>
  );
}

interface MessagePartItemProps {
  part: MessagePart;
  resolveFileUrl: MessagePartsViewProps["resolveFileUrl"];
}

function MessagePartItem({ part, resolveFileUrl }: MessagePartItemProps): ReactElement | null {
  const t = useUiT();
  const text = part.fragment?.text ?? "";
  const hasText = text.trim() !== "";
  const file = part.file;
  const fileNode = file ? renderMessagePartFile(part, file, resolveFileUrl, t) : null;
  const role = normalisePartValue(part.role);

  if (role === "QUOTED") {
    return <QuotedMessagePart text={text} file={fileNode} />;
  }

  if (role === "TITLE") {
    return (
      <div className="space-y-1">
        {hasText ? <h3 className="text-14 font-semibold leading-snug text-current">{text}</h3> : null}
        {fileNode}
      </div>
    );
  }

  if (role === "SIGNATURE") {
    return (
      <div className="space-y-1 text-current opacity-70">
        {hasText ? <div className="whitespace-pre-wrap">{text}</div> : null}
        {fileNode}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {hasText ? <div className="whitespace-pre-wrap">{text}</div> : null}
      {fileNode}
    </div>
  );
}

interface QuotedMessagePartProps {
  text: string;
  file: ReactNode;
}

function QuotedMessagePart({ text, file }: QuotedMessagePartProps): ReactElement | null {
  const t = useUiT();
  const [open, setOpen] = useState(false);
  const contentId = useId();
  if (text.trim() === "" && !file) return null;
  return (
    <div className="space-y-1">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen((current) => !current)}
        className="inline-flex h-6 items-center rounded-full border border-current/20 px-2 text-2xs font-medium text-current opacity-75 outline-none transition-opacity hover:opacity-100 focus-visible:focus-ring"
      >
        {open ? t("message.parts.hideQuoted") : t("message.parts.showQuoted")}
      </button>
      {open ? (
        <blockquote id={contentId} className="space-y-1 border-l-2 border-current pl-3 text-current opacity-75">
          {text.trim() !== "" ? <div className="whitespace-pre-wrap">{text}</div> : null}
          {file}
        </blockquote>
      ) : null}
    </div>
  );
}

function renderMessagePartFile(
  part: MessagePart,
  file: MessagePartFile,
  resolveFileUrl: MessagePartsViewProps["resolveFileUrl"],
  t: ReturnType<typeof useUiT>,
): ReactElement {
  const url = safeMessagePartUrl(resolveFileUrl(file));
  const label = file.title || file.filename || t("message.parts.attachment");
  const mime = normaliseMime(file.mime_type?.mime_type);
  if (
    url &&
    normalisePartValue(part.disposition) === "INLINE" &&
    Boolean(part.cid?.trim()) &&
    isImageMime(mime)
  ) {
    return (
      <figure className="my-1 max-w-full">
        <img
          src={url}
          alt={label || t("message.parts.inlineImage")}
          loading="lazy"
          className="max-h-96 max-w-full rounded-6 border border-current/20 object-contain"
        />
      </figure>
    );
  }

  const chip = (
    <MessageAttachmentChip
      icon={<Glyph decorative name="attachment" />}
      remove={
        typeof file.size_bytes === "number" ? (
          <span className="shrink-0 text-2xs opacity-70">{formatSize(file.size_bytes)}</span>
        ) : undefined
      }
    >
      {label}
    </MessageAttachmentChip>
  );
  if (!url) return chip;
  return (
    <a href={url} download={file.filename ?? undefined} className="inline-flex max-w-full align-middle">
      {chip}
    </a>
  );
}

function hasRenderableMessagePart(part: MessagePart): boolean {
  if (normalisePartValue(part.role) === "HEADER") return false;
  return (part.fragment?.text ?? "").trim() !== "" || (part.file !== null && part.file !== undefined);
}

function messagePartKey(part: MessagePart, index: number): string {
  return part.id || String(index);
}

function normalisePartValue(value: string | null | undefined): string {
  return (value ?? "").trim().toUpperCase();
}

function safeMessagePartUrl(url: string | null | undefined): string | undefined {
  const value = url?.trim();
  if (!value) return undefined;
  if (value.startsWith("/") && !value.startsWith("//")) return value;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? value : undefined;
  } catch {
    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Reactions
// ---------------------------------------------------------------------------

export interface Reaction {
  /** The reaction glyph (an emoji). */
  reaction: string;
  /** How many parties reacted with it. */
  count: number;
  /** Whether the current user is among them — sets `aria-pressed` + the active fill. */
  active?: boolean;
  /** Optional tooltip (e.g. the list of reactors). */
  title?: string;
}

interface ReactionGroupHandle {
  display_name?: string | null;
  value?: string | null;
}

export interface ReactionGroup {
  reaction: string;
  count: number;
  self_reacted?: boolean;
  handles: readonly ReactionGroupHandle[];
}

export interface ReactionGroupCopy {
  count: (
    values: Record<string, string> & { reaction: string; count: string },
  ) => string;
  named: (
    values: Record<string, string> & { reaction: string; names: string },
  ) => string;
}

/**
 * Projects transport reaction groups into the presentational `ReactionBar`
 * contract. The caller supplies translated copy formatters so the shared owner
 * keeps the name/no-name grammar while every product routes wording through its
 * own statically discoverable i18n keys.
 */
export function reactionsFromGroups(
  groups: readonly ReactionGroup[],
  copy: ReactionGroupCopy,
): Reaction[] {
  return groups.map((group) => {
    const names = group.handles
      .map((handle) => handle.display_name || handle.value || "")
      .filter((value) => value.trim() !== "");
    return {
      reaction: group.reaction,
      count: group.count,
      active: group.self_reacted,
      title:
        names.length === 0
          ? copy.count({
              reaction: group.reaction,
              count: group.count.toLocaleString(),
            })
          : copy.named({
              reaction: group.reaction,
              names: names.join(", "),
            }),
    };
  });
}

interface ReactionPillProps {
  /** The reaction glyph (an emoji). */
  reaction: string;
  /** The reactor count — omit for a count-less picker pill. */
  count?: number;
  active?: boolean;
  title?: string;
  disabled?: boolean;
  onToggle?: (reaction: string) => void;
}

/** One reaction toggle pill — the single owner of the reaction-pill markup and the
 *  `"👍 reaction[, 3]"` accessible-name convention shared by `ReactionBar` (with counts)
 *  and `ReactionPicker` (count-less). `aria-pressed` reflects the current user's own
 *  reaction; the count span renders only when a count is given. */
function ReactionPill({ reaction, count, active, title, disabled, onToggle }: ReactionPillProps): ReactElement {
  const t = useUiT();
  const label =
    count === undefined
      ? t("message.reaction.pill", { reaction })
      : t("message.reaction.pillCount", { reaction, count });
  return (
    <button
      type="button"
      disabled={disabled || !onToggle}
      title={title}
      aria-pressed={active}
      aria-label={label}
      onClick={onToggle ? () => onToggle(reaction) : undefined}
      className={cn(
        "inline-flex h-7 items-center gap-1 rounded-full border px-2 text-12 outline-none transition-colors focus-visible:focus-ring disabled:opacity-60",
        active
          ? "border-brand bg-brand-soft text-brand-soft-text"
          : "border-border-subtle bg-sheet-2 text-fg-muted hover:bg-inset hover:text-fg",
      )}
    >
      <span aria-hidden="true">{reaction}</span>
      {count === undefined ? null : <span aria-hidden="true">{count.toLocaleString()}</span>}
    </button>
  );
}

export interface ReactionBarProps {
  /** The reaction groups to render as toggle pills. */
  reactions: Reaction[];
  /** Toggles a reaction on click. When omitted the pills render inert (display only). */
  onToggle?: (reaction: string) => void;
  /** Disables every pill while a reaction mutation is in flight. */
  busy?: boolean;
  /** Accessible name for the group of reaction pills. */
  label?: string;
  className?: string;
}

/** The reaction pills under a message: one toggle button per reaction group. Each pill
 *  carries an accessible name (`"👍 reaction, 3"`) so the glyph + count read correctly to
 *  assistive tech, and `aria-pressed` reflects the current user's own reaction. Pure
 *  presentation — the consumer owns the reaction mutation via `onToggle`. */
export function ReactionBar({ reactions, onToggle, busy, label, className }: ReactionBarProps): ReactElement | null {
  if (reactions.length === 0) return null;
  return (
    <div role="group" aria-label={label} className={cn("flex flex-wrap gap-1", className)}>
      {reactions.map(({ reaction, count, active, title }) => (
        <ReactionPill
          key={reaction}
          reaction={reaction}
          count={count}
          active={active}
          title={title}
          disabled={busy}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
}

export interface ReactionPickerProps {
  /** The emoji options offered as a quick-add palette. */
  options: readonly string[];
  /** Reactions the current user has already applied — renders the matching pill pressed. */
  active?: readonly string[];
  /** Toggles a reaction on click. When omitted the pills render inert (display only). */
  onToggle?: (reaction: string) => void;
  /** Disables every pill while a reaction mutation is in flight. */
  busy?: boolean;
  /** Accessible name for the palette group. */
  label?: string;
  className?: string;
}

/** A quick-reaction *add* palette: the same reaction pills as `ReactionBar` but count-less,
 *  one per offered emoji, each toggling the current user's reaction. Shares `ReactionPill`
 *  so the pill markup and the accessible-name convention never diverge from the bar — the
 *  consumer no longer hand-rolls a divergent pill. Pure presentation; the consumer owns the
 *  reaction mutation via `onToggle`. */
export function ReactionPicker({
  options,
  active,
  onToggle,
  busy,
  label,
  className,
}: ReactionPickerProps): ReactElement | null {
  if (options.length === 0) return null;
  const activeSet = new Set(active);
  return (
    <div role="group" aria-label={label} className={cn("flex flex-wrap gap-1", className)}>
      {options.map((reaction) => (
        <ReactionPill
          key={reaction}
          reaction={reaction}
          active={activeSet.has(reaction)}
          disabled={busy}
          onToggle={onToggle}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Actions (hover row)
// ---------------------------------------------------------------------------

export interface MessageActionsProps extends Omit<HTMLAttributes<HTMLDivElement>, "role"> {
  /** CSS alignment of the row within its message — `start` (lead left) or `end`
   *  (trail right). Alignment only; carries no role/direction semantics. */
  align?: "start" | "end";
}

/** A presentational action row under a message (copy, reply, edit, …). Reveals on
 *  hover/focus of the enclosing `group` message and aligns per `align`. Transport-
 *  agnostic: the consumer composes plain buttons or a binding's action controls inside
 *  it. */
export function MessageActions({ align = "start", className, children, ...props }: MessageActionsProps): ReactElement {
  return (
    <div
      className={cn(
        "mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100",
        align === "end" ? "justify-end" : "justify-start",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

export interface MessageComposerProps extends HTMLAttributes<HTMLDivElement> {
  input?: ReactNode;
  actions?: ReactNode;
  /** An attachment row above the input — chips for pending images / the current-view
   *  record (`MessageAttachmentChip`). Renders only when present. */
  attachments?: ReactNode;
  /** Keyboard-shortcut hint in the footer row, left of the actions (`<MessageComposerHint/>`).
   *  When set, the footer row renders even without actions. */
  hint?: ReactNode;
}

/** Composer frame: an optional attachment chip row, then an input slot (a textarea, or a
 *  binding's input primitive), above a footer row carrying the hint and the send/cancel
 *  actions. */
export function MessageComposer({
  input,
  actions,
  attachments,
  hint,
  className,
  children,
  ...props
}: MessageComposerProps): ReactElement {
  return (
    <div className={cn("space-y-2", className)} {...props}>
      {attachments ? (
        <div className="flex flex-wrap items-center gap-1.5">{attachments}</div>
      ) : null}
      {input ?? children ?? <Textarea rows={3} resize="none" />}
      {hint || actions ? (
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">{hint}</div>
          {actions ? <div className="flex shrink-0 items-center gap-1.5">{actions}</div> : null}
        </div>
      ) : null}
    </div>
  );
}

export interface MessageComposerHintProps {
  children?: ReactNode;
  className?: string;
}

/** The keyboard-shortcut affordance under a composer; defaults to "⏎ send · ⇧⏎ newline".
 *  Pure presentation — the real keybindings live with the input the composer renders. */
export function MessageComposerHint({ children, className }: MessageComposerHintProps): ReactElement {
  const t = useUiT();
  return (
    <span className={cn(textRoleVariants({ role: "caption" }), "flex items-center gap-1.5", className)}>
      {children ?? (
        <>
          <Kbd size="sm">⏎</Kbd>
          <span>{t("message.composer.send")}</span>
          <span aria-hidden className="opacity-60">
            ·
          </span>
          <Kbd size="sm">⇧⏎</Kbd>
          <span>{t("message.composer.newline")}</span>
        </>
      )}
    </span>
  );
}

/** The shared textarea class for a message composer input — lets a consumer style a
 *  binding's input primitive like the default `<Textarea>`. */
export const messageComposerInputClassName = textareaVariants({ size: "md", resize: "none" });

export interface MessageAttachmentChipProps {
  /** A leading glyph slot (e.g. paperclip / file). */
  icon?: ReactNode;
  /** The chip label (a filename, "Current view", …). */
  children: ReactNode;
  /** A trailing remove control slot — the consumer passes the binding's remove button. */
  remove?: ReactNode;
  /** When set, the label becomes a button (e.g. to inspect the attachment). */
  onClick?: () => void;
  tone?: ChipTone;
  className?: string;
}

/** A composer attachment chip: a leading icon, a truncated label, and an optional remove
 *  control. Presentation only — the consumer supplies the icon glyph and wires the remove
 *  binding (or a plain button) into the `remove` slot; an `onClick` makes the label a
 *  button (the view-record chip uses it to open an inspector). */
export function MessageAttachmentChip({
  icon,
  children,
  remove,
  onClick,
  tone = "neutral",
  className,
}: MessageAttachmentChipProps): ReactElement {
  const body = (
    <>
      {icon ? <span className="flex shrink-0 items-center">{icon}</span> : null}
      <span className="truncate">{children}</span>
    </>
  );
  return (
    <Chip tone={tone} size="sm" className={cn("gap-1", className)}>
      {onClick ? (
        <button type="button" onClick={onClick} className="flex min-w-0 items-center gap-1">
          {body}
        </button>
      ) : (
        body
      )}
      {remove}
    </Chip>
  );
}
