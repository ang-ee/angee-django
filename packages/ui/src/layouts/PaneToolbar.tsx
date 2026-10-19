import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { createLayoutBand } from "./layout-band";

/**
 * The one dense toolbar every side pane renders into: explorer panes on the left,
 * context-aside tabs, drill-ins and drawers on the right. A pane host mounts the
 * row (32px) above its scroll area and may lead it with a way back and a title;
 * the mounted pane publishes the rest live through `PaneToolbar`: scope · selector
 * · search · actions · overflow. Each host owns its own band registry, so a
 * retained but hidden pane never publishes into another pane's row.
 */
const band = createLayoutBand("div", "flex min-w-0 flex-1 items-center gap-1");

export interface PaneToolbarHostProps {
  children: React.ReactNode;
  /** A way back, shown first; a drill-in's host gives the origin tab's label. */
  back?: { label: React.ReactNode; onClick: () => void };
  /** The pane's title, after the way back; strip tabs name themselves in the strip. */
  title?: React.ReactNode;
  className?: string;
}

/** A side pane's frame: the toolbar row, then the pane's content filling the rest. */
export function PaneToolbarHost({ children, back, title, className }: PaneToolbarHostProps): React.ReactElement {
  const [host, setHost] = React.useState<HTMLElement | null>(null);
  return (
    <div className={cn("flex min-h-0 min-w-0 flex-1 flex-col", className)}>
      <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border-subtle bg-sheet px-2">
        {back ? (
          <button type="button" onClick={back.onClick} className="flex shrink-0 items-center text-13 text-link hover:underline">
            <Glyph name="chevron-left" />{back.label}
          </button>
        ) : null}
        {title ? <span className="min-w-0 shrink truncate text-13 font-semibold">{title}</span> : null}
        <div ref={setHost} className="flex min-w-0 flex-1 items-center gap-1" />
      </div>
      <band.Provider host={host}>{children}</band.Provider>
    </div>
  );
}

export interface PaneToolbarAction {
  icon: string;
  label: string;
  onClick?: () => void;
  tone?: "danger";
  /** Show the label beside the icon; the default is an icon-only button. */
  text?: boolean;
  disabled?: boolean;
}

export interface PaneToolbarProps {
  /** The pane's filter: scope chips, a lens row. */
  scope?: React.ReactNode;
  /** A switcher when the pane holds several of a thing (root, agent, run). */
  selector?: React.ReactNode;
  /** A search control; `true` renders the shared icon that the pane wires through `onSearch`. */
  search?: boolean | React.ReactNode;
  onSearch?: () => void;
  actions?: readonly PaneToolbarAction[];
  /** The overflow menu trigger; `true` renders the shared icon that the pane wires through `onOverflow`. */
  overflow?: boolean | React.ReactNode;
  onOverflow?: () => void;
  className?: string;
}

/** The pane's live toolbar content, portaled into its host's row. */
export function PaneToolbar({ scope, selector, search = false, onSearch, actions, overflow = false, onOverflow, className }: PaneToolbarProps): React.ReactElement | React.ReactPortal | null {
  return (
    <band.Band className={className}>
      {selector}
      {scope ? <div className="flex min-w-0 items-center gap-1 overflow-hidden [&>*]:shrink-0">{scope}</div> : null}
      <span className="ml-auto flex shrink-0 items-center gap-0.5">
        {search === true ? <PaneToolbarIconButton icon="search" label="Search" onClick={onSearch} /> : search || null}
        {actions?.map((action) => action.text ? (
          <Button key={action.label} size="sm" variant={action.tone === "danger" ? "danger" : "ghost"} disabled={action.disabled} onClick={action.onClick} className="h-6 whitespace-nowrap px-1.5 text-12">
            <Glyph name={action.icon} /> {action.label}
          </Button>
        ) : (
          <PaneToolbarIconButton key={action.label} icon={action.icon} label={action.label} tone={action.tone} disabled={action.disabled} onClick={action.onClick} />
        ))}
        {overflow === true ? <PaneToolbarIconButton icon="more-horizontal" label="More" onClick={onOverflow} /> : overflow || null}
      </span>
    </band.Band>
  );
}

export interface PaneToolbarIconButtonProps {
  icon: string;
  label: string;
  onClick?: () => void;
  tone?: "danger";
  active?: boolean;
  badge?: number;
  disabled?: boolean;
}

/** The toolbar's 28px icon button, with an optional count badge (needs you). */
export function PaneToolbarIconButton({ icon, label, onClick, tone, active, badge, disabled }: PaneToolbarIconButtonProps): React.ReactElement {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "relative grid size-7 place-items-center rounded-6 disabled:opacity-50",
        active ? "bg-canvas text-fg" : tone === "danger" ? "text-danger-text hover:bg-danger-bg" : "text-fg-muted hover:bg-canvas hover:text-fg",
      )}
    >
      <Glyph name={icon} />
      {badge ? <span className="absolute -right-0.5 -top-0.5 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-warning-text px-0.5 text-[10px] font-semibold leading-none text-white">{badge}</span> : null}
    </button>
  );
}
