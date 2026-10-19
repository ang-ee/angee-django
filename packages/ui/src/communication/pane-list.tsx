import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { Chip } from "../ui/chip";
import { StatusIcon, type StatusIconTone } from "../ui/status-icon";

/**
 * The one row anatomy of every side pane list: the Timeline rail, Tasks, the
 * Related groups, a drill-in's references. A row is a pointer: glyph, `title ·
 * detail`, a right-aligned meta (time, due), a muted second line and one verb
 * that shows on hover. It is a link when its target has a page, a button when it
 * opens something beside it. Labels name days and sections above rows. Type
 * sits on the 13/12 scale; nothing in a pane list picks its own size.
 */

export interface PaneRowProps {
  /** The leading glyph, through the status icon so tones agree across panes. */
  icon?: string;
  tone?: StatusIconTone;
  /** A custom leading control (a checkbox) in place of the glyph. */
  leading?: React.ReactNode;
  title: React.ReactNode;
  /** Follows the title on the same line, muted. */
  detail?: React.ReactNode;
  /** Right-aligned on the first line: a time, a due date, a size. */
  meta?: React.ReactNode;
  /** The muted second line: actor, folded summary, assignee. */
  sub?: React.ReactNode;
  /** The row's one verb, at the end of the second line, revealed on hover. */
  verb?: React.ReactNode;
  /** Draws the warning dot and emphasises the title: this row waits on the viewer. */
  needsMe?: boolean;
  selected?: boolean;
  /** An in-app href makes the row a link (copy, open in new tab); without one it is a button. */
  href?: string;
  onOpen?: () => void;
  /** The native tooltip, usually the verb. */
  label?: string;
  className?: string;
}

const ROW_CLASS = "group/row flex w-full gap-2 rounded-6 px-1 py-1.5 text-left hover:bg-canvas/70 aria-[current=true]:bg-canvas";

export function PaneRow({
  icon, tone = "muted", leading, title, detail, meta, sub, verb, needsMe = false, selected, href, onOpen, label, className,
}: PaneRowProps): React.ReactElement {
  const glyph = (
    <span className="relative mt-0.5 shrink-0">
      <StatusIcon tone={tone} icon={icon ?? "activity"} size="sm" />
      {needsMe ? <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-warning-text ring-2 ring-sheet" /> : null}
    </span>
  );
  const body = (
    <span className="min-w-0 flex-1">
      <span className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 truncate text-13">
          <span className={needsMe ? "font-semibold" : "font-medium"}>{title}</span>
          {detail ? <span className="text-fg-muted"> · {detail}</span> : null}
        </span>
        {meta ? <span className="shrink-0 text-12 tabular-nums text-fg-muted">{meta}</span> : null}
      </span>
      {sub || verb ? (
        <span className="flex items-center gap-1 text-12 text-fg-muted">
          <span className="min-w-0 truncate">{sub}</span>
          {verb ? <span className="ml-auto flex shrink-0 items-center text-link opacity-0 group-hover/row:opacity-100 group-focus-visible/row:opacity-100">{verb}</span> : null}
        </span>
      ) : null}
    </span>
  );
  const common = { title: label, "aria-current": selected ? true : undefined } as const;
  // A custom leading control is interactive on its own, so it sits beside the
  // row's link or button rather than inside it.
  if (leading) {
    return (
      <span className={cn(ROW_CLASS, className)}>
        <span className="mt-0.5 shrink-0">{leading}</span>
        {href
          ? <a href={href} {...common} className="flex min-w-0 flex-1 text-left" onClick={onOpen ? (event) => { event.preventDefault(); onOpen(); } : undefined}>{body}</a>
          : <button type="button" {...common} className="flex min-w-0 flex-1 text-left" onClick={onOpen}>{body}</button>}
      </span>
    );
  }
  if (href) {
    return <a href={href} {...common} className={cn(ROW_CLASS, className)} onClick={onOpen ? (event) => { event.preventDefault(); onOpen(); } : undefined}>{glyph}{body}</a>;
  }
  return <button type="button" {...common} className={cn(ROW_CLASS, className)} onClick={onOpen}>{glyph}{body}</button>;
}

/** The rows of one pane list. */
export function PaneRows({ children, className, ...rest }: React.HTMLAttributes<HTMLUListElement>): React.ReactElement {
  return <ul className={cn("flex flex-col", className)} {...rest}>{children}</ul>;
}

export interface PaneLabelProps {
  children: React.ReactNode;
  count?: number;
  /** Folded sections open and close from their label. */
  folded?: boolean;
  onToggle?: () => void;
  /** The label's one verb (a group's "Upload", "Bind"). */
  action?: React.ReactNode;
  className?: string;
}

/** A day or section label above rows: 12px, muted, with an optional count, fold and verb. */
export function PaneLabel({ children, count, folded, onToggle, action, className }: PaneLabelProps): React.ReactElement {
  const text = (
    <>
      {onToggle ? <Glyph name={folded ? "chevron-right" : "chevron-down"} /> : null}
      <span className="truncate">{children}</span>
      {typeof count === "number" ? <span className="font-normal">· {count}</span> : null}
    </>
  );
  return (
    <li className={cn("flex items-center gap-1 px-1 pb-1 pt-4 text-12 font-medium text-fg-muted first:pt-1", className)}>
      {onToggle ? (
        <button type="button" aria-expanded={!folded} onClick={onToggle} className="flex min-w-0 items-center gap-1 rounded-4 hover:text-fg">{text}</button>
      ) : text}
      {action ? <span className="ml-auto flex shrink-0 items-center text-link">{action}</span> : null}
    </li>
  );
}

/** A label's verb: a small link-coloured button with a glyph. */
export function PaneLabelAction({ icon, children, onClick, disabled }: { icon?: string; children: React.ReactNode; onClick?: () => void; disabled?: boolean }): React.ReactElement {
  return (
    <button type="button" onClick={onClick} disabled={disabled} className="flex items-center gap-0.5 rounded-6 px-1 text-12 font-normal text-link hover:bg-canvas disabled:opacity-50">
      {icon ? <Glyph name={icon} /> : null}{children}
    </button>
  );
}

export interface ScopeChipProps {
  label: React.ReactNode;
  count?: number;
  active: boolean;
  tone?: "warning";
  onClick: () => void;
}

/** A pane toolbar's scope chip: pressed = the active filter; the count rides inside. */
export function ScopeChip({ label, count, active, tone, onClick }: ScopeChipProps): React.ReactElement {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} className="rounded-full">
      <Chip size="sm" tone={active ? "brand" : tone ?? "muted"} variant={active ? "solid" : "soft"} className="cursor-pointer whitespace-nowrap">
        {label}{typeof count === "number" ? <span className={active ? "ml-1 opacity-80" : "ml-1 text-fg-muted"}>{count}</span> : null}
      </Chip>
    </button>
  );
}
