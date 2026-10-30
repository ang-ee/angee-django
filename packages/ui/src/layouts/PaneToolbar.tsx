import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { ActionMenu, ActionTrigger } from "../toolbars/ActionMenu";
import { Button } from "../ui/button";
import { DropdownMenu } from "../ui/dropdown-menu";
import { ToolbarButton, ToolbarRoot } from "../ui/toolbar";
import { createLayoutBand } from "./layout-band";

/**
 * The one dense toolbar every side pane renders into: explorer panes on the left,
 * context-aside tabs, drill-ins and drawers on the right. A pane host mounts the
 * row (32px) above its scroll area and may lead it with a way back and a title;
 * the mounted pane publishes the rest live through `PaneToolbar`: scope · selector
 * · search · actions · overflow. Each host owns its own band registry, so a
 * retained but hidden pane never publishes into another pane's row. The band is a
 * `Toolbar` (roving focus); its controls are the one `Button` at `iconSm` (28px).
 */
const band = createLayoutBand(ToolbarRoot, "flex w-full min-w-0 shrink-0 items-center gap-1");

export interface PaneToolbarHostProps extends Pick<React.HTMLAttributes<HTMLDivElement>, "role" | "aria-label"> {
  children: React.ReactNode;
  /** A way back, shown first; a drill-in's host gives the origin tab's label. */
  back?: { label: React.ReactNode; onClick: () => void };
  /** The pane's title, after the way back; strip tabs name themselves in the strip. */
  title?: React.ReactNode;
  className?: string;
}

/** A side pane's frame: the toolbar row, then the pane's content filling the rest.
 * @owner PaneToolbar
 */
export function PaneToolbarHost({ children, back, title, className, ...rest }: PaneToolbarHostProps): React.ReactElement {
  const [host, setHost] = React.useState<HTMLElement | null>(null);
  return (
    <div className={cn("flex h-full min-h-0 min-w-0 flex-1 flex-col", className)} {...rest}>
      {/* The row shows only while something sits in it: a way back, a title or a published band. */}
      <div className="hidden h-8 shrink-0 items-center gap-1 border-b border-border-subtle bg-sheet px-2 has-[>*:not(:empty)]:flex">
        {back ? (
          <Button type="button" variant="link" size="sm" onClick={back.onClick} className="shrink-0 gap-0 px-0 text-13 hover:underline">
            <Glyph name="chevron-left" />{back.label}
          </Button>
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
  /** A count on the icon button (unread, needs you). */
  badge?: number;
  /** The control's toggle state (a search field that is open). */
  pressed?: boolean;
  disabled?: boolean;
  /** Make the action a menu: the button opens these items instead of running `onClick`. */
  items?: readonly PaneToolbarMenuItem[];
}

export interface PaneToolbarProps {
  /** The pane's filter: scope chips, a lens row. */
  scope?: React.ReactNode;
  /** A switcher when the pane holds several of a thing (root, agent, run). */
  selector?: React.ReactNode;
  /** A search control; `true` renders the shared icon that the pane wires through `onSearch`. */
  search?: boolean | React.ReactNode;
  onSearch?: () => void;
  /** The pane's verbs: descriptors for the shared buttons, or the pane's own controls (a link, an action bar). */
  actions?: readonly PaneToolbarAction[] | React.ReactNode;
  /** The overflow menu trigger; `true` renders the shared icon that the pane wires through `onOverflow`. */
  overflow?: boolean | React.ReactNode;
  onOverflow?: () => void;
  className?: string;
}

/** The pane's live toolbar content, portaled into its host's row.
 * @kind region
 * @region pane-toolbar-band
 */
export function PaneToolbar({ scope, selector, search = false, onSearch, actions, overflow = false, onOverflow, className }: PaneToolbarProps): React.ReactElement | React.ReactPortal | null {
  const t = useUiT();
  return (
    <band.Band className={className}>
      {selector}
      {scope ? <div className="flex min-w-0 items-center gap-1 overflow-hidden [&>*]:shrink-0">{scope}</div> : null}
      <span className="ml-auto flex shrink-0 items-center gap-0.5">
        {search === true ? <PaneToolbarButton icon="search" label={t("paneToolbar.search")} onClick={onSearch} /> : search || null}
        {Array.isArray(actions) ? (actions as readonly PaneToolbarAction[]).map((action) => action.items ? (
          <PaneToolbarMenu key={action.label} label={action.label} icon={action.icon} text={action.text}
            badge={action.badge} disabled={action.disabled} items={action.items} />
        ) : (
          <PaneToolbarButton key={action.label} icon={action.icon} label={action.label} text={action.text} tone={action.tone}
            badge={action.badge} pressed={action.pressed} disabled={action.disabled} onClick={action.onClick} />
        )) : (actions as React.ReactNode)}
        {overflow === true ? <PaneToolbarButton icon="more-horizontal" label={t("paneToolbar.more")} onClick={onOverflow} /> : overflow || null}
      </span>
    </band.Band>
  );
}

/** One descriptor control: the toolbar `Button` at `iconSm`, or a small labelled verb.
 * @owner PaneToolbar
 */
function PaneToolbarButton({ icon, label, text, tone, badge, pressed, disabled, onClick }: Omit<PaneToolbarAction, "items">): React.ReactElement {
  if (text) {
    return (
      <ToolbarButton size="sm" variant={tone === "danger" ? "danger" : "ghost"} disabled={disabled} onClick={onClick} badge={badge} pressed={pressed} className="h-control-xs whitespace-nowrap px-1.5 text-12">
        <Glyph name={icon} /> {label}
      </ToolbarButton>
    );
  }
  return (
    <ToolbarButton size="iconSm" variant={tone === "danger" ? "danger" : "ghost"} aria-label={label} title={label} disabled={disabled} onClick={onClick} badge={badge} pressed={pressed}>
      <Glyph name={icon} />
    </ToolbarButton>
  );
}

export interface PaneToolbarMenuItem {
  icon?: string;
  label: string;
  onClick?: () => void;
  tone?: "danger";
  disabled?: boolean;
}

export interface PaneToolbarMenuRadioGroup {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  items: readonly { value: string; label: React.ReactNode; disabled?: boolean }[];
}

export interface PaneToolbarMenuProps {
  /** The menu's name: the icon button's accessible label, or the labelled trigger's text. */
  label?: string;
  /** The trigger's glyph; the shared overflow icon when omitted. */
  icon?: string;
  /** Show the label beside the glyph (a labelled "New ▾" menu). */
  text?: boolean;
  badge?: number;
  disabled?: boolean;
  items?: readonly PaneToolbarMenuItem[];
  /** Radio groups before the items: a pane's order, grouping or coverage choices. */
  groups?: readonly PaneToolbarMenuRadioGroup[];
}

/** The toolbar's overflow: one `ActionMenu` behind the shared icon, with the pane's secondary verbs.
 * @owner PaneToolbar
 */
export function PaneToolbarMenu({ label, icon = "more-horizontal", text = false, badge, disabled, items = [], groups = [] }: PaneToolbarMenuProps): React.ReactElement | null {
  const t = useUiT();
  if (items.length === 0 && groups.length === 0) return null;
  const name = label ?? t("paneToolbar.more");
  return (
    <ActionMenu align="end" glyph={icon} disabled={disabled} badge={badge}
      {...(text
        ? { label: <>{name}<Glyph name="chevron-down" className="opacity-60" /></>, size: "sm" as const, className: "h-control-xs whitespace-nowrap px-1.5 text-12" }
        : { label: null, "aria-label": name, size: "iconSm" as const })}>
      {groups.map((group) => <DropdownMenu.RadioGroup key={group.label} aria-label={group.label}
        value={group.value} onValueChange={group.onValueChange}>
        <DropdownMenu.Label>{group.label}</DropdownMenu.Label>
        {group.items.map((item) => <DropdownMenu.RadioItem key={item.value} value={item.value} disabled={item.disabled}>
          <DropdownMenu.RadioItemIndicator /><span>{item.label}</span>
        </DropdownMenu.RadioItem>)}
      </DropdownMenu.RadioGroup>)}
      {items.map((item) => (
        <ActionTrigger key={item.label} glyph={item.icon} disabled={item.disabled} variant={item.tone === "danger" ? "danger" : "secondary"} onClick={item.onClick}>
          {item.label}
        </ActionTrigger>
      ))}
    </ActionMenu>
  );
}
