import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { useInAppLink } from "../lib/in-app-link";
import { Chip } from "../ui/chip";
import { Popover, type PopoverRootProps } from "../ui/popover";
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
  /**
   * The row's one verb at the end of the row, revealed on hover. A string names
   * what opening the row does (a hint beside the row's own control); a node is
   * the verb's own control, rendered beside the row's link or button.
   */
  verb?: React.ReactNode;
  /** Draws the warning dot and emphasises the title: this row waits on the viewer. */
  needsMe?: boolean;
  selected?: boolean;
  /** A destination makes the row a native link, with the host's navigation and preload behavior. */
  href?: string;
  target?: React.HTMLAttributeAnchorTarget;
  rel?: string;
  /** For rows that open an action without a destination. Otherwise the row is non-interactive. */
  onOpen?: () => void;
  /** The native tooltip, usually the verb. */
  label?: string;
  className?: string;
}

// The row's own control carries `aria-current`; the wrapper reads it for the selected fill.
const ROW_CLASS = "group/row flex w-full gap-2 rounded-6 px-1 py-1.5 text-left hover:bg-canvas/70 has-[[aria-current=true]]:selected";
const TARGET_CLASS = "flex min-w-0 flex-1 gap-2 rounded-4 text-left outline-none focus-visible:focus-ring";

/**
 * @kind fragment
 */
export function PaneRow({
  icon, tone = "muted", leading, title, detail, meta, sub, verb, needsMe = false, selected, href, target, rel, onOpen, label, className,
}: PaneRowProps): React.ReactElement {
  const link = useInAppLink(href);
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
      {sub ? <span className="block min-w-0 truncate text-12 text-fg-muted">{sub}</span> : null}
    </span>
  );
  const common = { title: label, "aria-current": selected ? true : undefined } as const;
  // The leading control and the verb are interactive on their own, so they sit
  // beside the row's link or button rather than inside it.
  const control = href
    ? <a href={href} target={target} rel={rel} {...common} {...link} className={TARGET_CLASS}>{leading ? null : glyph}{body}</a>
    : onOpen ? <button type="button" {...common} className={TARGET_CLASS} onClick={onOpen}>{leading ? null : glyph}{body}</button>
      : <span {...common} className={TARGET_CLASS}>{leading ? null : glyph}{body}</span>;
  return (
    <span className={cn(ROW_CLASS, className)}>
      {leading ? <span className="mt-0.5 shrink-0">{leading}</span> : null}
      {control}
      {verb ? (
        <span className="flex shrink-0 items-end text-12 text-link opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100 [&>*]:rounded-4 [&>*]:hover:bg-canvas">
          {typeof verb === "string" ? <span aria-hidden="true">{verb}</span> : verb}
        </span>
      ) : null}
    </span>
  );
}

/** The rows of one pane list.
 * @owner PaneRow
 */
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

/** A day or section label above rows: 12px, muted, with an optional count, fold and verb.
 * @kind fragment
 */
export function PaneLabel({ children, count, folded, onToggle, action, className }: PaneLabelProps): React.ReactElement {
  const text = (
    <>
      {onToggle ? <Glyph name={folded ? "chevron-right" : "chevron-down"} /> : null}
      <span className="truncate">{children}</span>
      {typeof count === "number" ? <span className="font-normal">· {count}</span> : null}
    </>
  );
  return (
    <li className={cn("group flex items-center gap-1 px-1 pb-1 pt-4 text-12 font-medium text-fg-muted first:pt-1", className)}>
      {onToggle ? (
        <button type="button" aria-expanded={!folded} onClick={onToggle} className="flex min-w-0 items-center gap-1 rounded-4 hover:text-fg">{text}</button>
      ) : text}
      {/* The verb shows on hover or focus, so a list of labels reads as labels, not as a column of links. */}
      {action ? <span className="ml-auto flex shrink-0 items-center text-link opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">{action}</span> : null}
    </li>
  );
}

/** A label's verb: a small link-coloured button with a glyph.
 * @owner PaneLabel
 */
export function PaneLabelAction({ icon, children, className, ...props }: React.ComponentPropsWithRef<"button"> & { icon?: string }): React.ReactElement {
  return (
    <button type="button" {...props} className={cn("flex items-center gap-0.5 rounded-6 px-1 text-12 font-normal text-link hover:bg-canvas disabled:opacity-50", className)}>
      {icon ? <Glyph name={icon} /> : null}{children}
    </button>
  );
}

/** A group's small action picker, using the native popover's controlled or uncontrolled lifetime.
 * @owner PaneLabel
 */
export function PaneLabelPopover({ icon, label, children, ...props }: Pick<PopoverRootProps, "open" | "onOpenChange"> & {
  icon: string;
  label: string;
  children: React.ReactNode;
}): React.ReactElement {
  return <Popover.Root {...props}>
    <Popover.Trigger render={<PaneLabelAction icon={icon}>{label}</PaneLabelAction>} />
    <Popover.Portal>
      <Popover.Positioner align="end" sideOffset={4}>
        <Popover.Content aria-label={label} className="flex w-64 flex-col gap-2 p-3 text-13">{children}</Popover.Content>
      </Popover.Positioner>
    </Popover.Portal>
  </Popover.Root>;
}

export interface ScopeChipProps {
  label: React.ReactNode;
  count?: number;
  active: boolean;
  tone?: "warning";
  onClick: () => void;
}

/** A pane toolbar's scope chip: the pressed `Chip`; the count rides inside.
 * @kind fragment
 */
export function ScopeChip({ label, count, active, tone, onClick }: ScopeChipProps): React.ReactElement {
  return (
    <Chip size="sm" tone={tone ?? "muted"} pressed={active} onClick={onClick} className="whitespace-nowrap">
      {label}{typeof count === "number" ? <span className="ml-1 tabular-nums opacity-70">{count}</span> : null}
    </Chip>
  );
}
