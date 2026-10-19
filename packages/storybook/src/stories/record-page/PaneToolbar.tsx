import * as React from "react";
import { Button, Chip, Glyph } from "@angee/ui";

/**
 * The one dense toolbar every side pane renders into (32px): back · title · scope · selector ·
 * search · actions · overflow. Explorer panes and context panes share it; a drill-in starts with
 * back and title. Slots are typed so a pane cannot invent a header of its own.
 */
export interface PaneToolbarProps {
  back?: { label: string; onClick?: () => void };
  title?: React.ReactNode;
  scope?: React.ReactNode;
  selector?: React.ReactNode;
  search?: boolean;
  actions?: readonly ToolbarAction[];
  overflow?: boolean;
}

export interface ToolbarAction { icon: string; label: string; tone?: "danger"; text?: boolean }

export function PaneToolbar({ back, title, scope, selector, search = true, actions, overflow = true }: PaneToolbarProps): React.ReactElement {
  return (
    <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border-subtle bg-sheet px-2">
      {back ? <button type="button" onClick={back.onClick} className="flex shrink-0 items-center text-13 text-link hover:underline"><Glyph name="chevron-left" />{back.label}</button> : null}
      {title ? <span className="min-w-0 truncate text-13 font-semibold">{title}</span> : null}
      {selector}
      {scope ? <div className="flex min-w-0 items-center gap-1 overflow-hidden [&>*]:shrink-0">{scope}</div> : null}
      <span className="ml-auto flex shrink-0 items-center gap-0.5">
        {search ? <IconButton icon="search" label="Search" /> : null}
        {actions?.map((a) => a.text
          ? <Button key={a.label} size="sm" variant={a.tone === "danger" ? "danger" : "ghost"} className="h-6 whitespace-nowrap px-1.5 text-12"><Glyph name={a.icon} /> {a.label}</Button>
          : <IconButton key={a.label} icon={a.icon} label={a.label} tone={a.tone} />)}
        {overflow ? <IconButton icon="more-horizontal" label="More" /> : null}
      </span>
    </div>
  );
}

export function IconButton({ icon, label, tone, active, badge }: { icon: string; label: string; tone?: "danger"; active?: boolean; badge?: number }): React.ReactElement {
  return (
    <button type="button" aria-label={label} title={label} aria-pressed={active}
      className={`relative grid size-7 place-items-center rounded-6 ${active ? "bg-canvas text-fg" : tone === "danger" ? "text-danger-text hover:bg-danger-bg" : "text-fg-muted hover:bg-canvas hover:text-fg"}`}>
      <Glyph name={icon} />
      {badge ? <span className="absolute -right-0.5 -top-0.5 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-warning-text px-0.5 text-[10px] font-semibold leading-none text-white">{badge}</span> : null}
    </button>
  );
}

export function ScopeChip({ label, count, active, tone = "muted" }: { label: string; count?: number; active?: boolean; tone?: "muted" | "brand" | "warning" }): React.ReactElement {
  return (
    <button type="button" aria-pressed={active} className="rounded-full">
      <Chip size="sm" tone={active ? "brand" : tone} variant={active ? "solid" : "soft"} className="cursor-pointer whitespace-nowrap text-12">
        {label}{count !== undefined ? <span className={active ? "ml-1 opacity-80" : "ml-1 text-fg-muted"}>{count}</span> : null}
      </Chip>
    </button>
  );
}

/** A scope row that never overflows: the first `max` chips, then one "Filter" menu. */
export function ScopeRow({ items, max = 3, active }: { items: readonly { id: string; label: string; count?: number; tone?: "warning" }[]; max?: number; active: string }): React.ReactElement {
  const shown = items.slice(0, max);
  const rest = items.length - shown.length;
  return (
    <>
      {shown.map((item) => <ScopeChip key={item.id} label={item.label} count={item.count} active={item.id === active} tone={item.tone} />)}
      {rest > 0 ? <button type="button" className="flex h-5 items-center gap-0.5 whitespace-nowrap rounded-full px-1.5 text-12 text-fg-muted hover:bg-canvas"><Glyph name="filter" />{rest} more</button> : null}
    </>
  );
}

/** The explorer's root selector: the collection or root and its current view in one control. */
export function RootSelector({ root, view }: { root: string; view?: string }): React.ReactElement {
  return (
    <button type="button" className="flex min-w-0 flex-1 items-center gap-1 rounded-6 px-1.5 py-0.5 text-13 font-medium hover:bg-canvas">
      <span className="truncate">{root}</span>
      {view ? <span className="truncate font-normal text-fg-muted">· {view}</span> : null}
      <Glyph name="chevron-down" />
    </button>
  );
}
