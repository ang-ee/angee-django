import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { DropdownMenu } from "../ui/dropdown-menu";
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

export interface PaneToolbarHostProps extends Pick<React.HTMLAttributes<HTMLDivElement>, "role" | "aria-label"> {
  children: React.ReactNode;
  /** A way back, shown first; a drill-in's host gives the origin tab's label. */
  back?: { label: React.ReactNode; onClick: () => void };
  /** The pane's title, after the way back; strip tabs name themselves in the strip. */
  title?: React.ReactNode;
  className?: string;
}

/** A side pane's frame: the toolbar row, then the pane's content filling the rest. */
export function PaneToolbarHost({ children, back, title, className, ...rest }: PaneToolbarHostProps): React.ReactElement {
  const [host, setHost] = React.useState<HTMLElement | null>(null);
  return (
    <div className={cn("flex h-full min-h-0 min-w-0 flex-1 flex-col", className)} {...rest}>
      {/* The row shows only while something sits in it: a way back, a title or a published band. */}
      <div className="hidden h-8 shrink-0 items-center gap-1 border-b border-border-subtle bg-sheet px-2 has-[>*:not(:empty)]:flex">
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
  /** A count on the icon button (unread, needs you). */
  badge?: number;
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
  /** The pane's verbs: descriptors for the shared buttons, or the pane's own controls (a link, an action bar). */
  actions?: readonly PaneToolbarAction[] | React.ReactNode;
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
        {Array.isArray(actions) ? (actions as readonly PaneToolbarAction[]).map((action) => action.text ? (
          <Button key={action.label} size="sm" variant={action.tone === "danger" ? "danger" : "ghost"} disabled={action.disabled} onClick={action.onClick} className="h-6 whitespace-nowrap px-1.5 text-12">
            <Glyph name={action.icon} /> {action.label}
          </Button>
        ) : (
          <PaneToolbarIconButton key={action.label} icon={action.icon} label={action.label} tone={action.tone} badge={action.badge} disabled={action.disabled} onClick={action.onClick} />
        )) : (actions as React.ReactNode)}
        {overflow === true ? <PaneToolbarIconButton icon="more-horizontal" label="More" onClick={onOverflow} /> : overflow || null}
      </span>
    </band.Band>
  );
}

export interface PaneToolbarIconButtonProps extends Omit<React.ComponentPropsWithRef<"button">, "children" | "onClick"> {
  icon: string;
  label: string;
  onClick?: () => void;
  tone?: "danger";
  active?: boolean;
  badge?: number;
  disabled?: boolean;
}

/** The toolbar's 28px icon button, with an optional count badge (needs you). */
export function PaneToolbarIconButton({ icon, label, onClick, tone, active, badge, disabled, className, ...props }: PaneToolbarIconButtonProps): React.ReactElement {
  return (
    <button
      {...props}
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "relative grid size-7 place-items-center rounded-6 disabled:opacity-50",
        active ? "bg-canvas text-fg" : tone === "danger" ? "text-danger-text hover:bg-danger-bg" : "text-fg-muted hover:bg-canvas hover:text-fg",
        className,
      )}
    >
      <Glyph name={icon} />
      {badge ? <span className="absolute -right-0.5 -top-0.5 grid h-3.5 min-w-3.5 place-items-center rounded-full bg-warning-text px-0.5 text-[10px] font-semibold leading-none text-white">{badge}</span> : null}
    </button>
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

/** The toolbar's overflow: one menu behind the shared icon, with the pane's secondary verbs. */
export function PaneToolbarMenu({ label, items = [], groups = [] }: {
  label?: string;
  items?: readonly PaneToolbarMenuItem[];
  groups?: readonly PaneToolbarMenuRadioGroup[];
}): React.ReactElement | null {
  const t = useUiT();
  if (items.length === 0 && groups.length === 0) return null;
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger render={<PaneToolbarIconButton icon="more-horizontal" label={label ?? t("paneToolbar.more")} />} />
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner sideOffset={4} align="end">
          <DropdownMenu.Content className="w-52">
            {groups.map((group) => <DropdownMenu.RadioGroup key={group.label} aria-label={group.label}
              value={group.value} onValueChange={group.onValueChange}>
              <DropdownMenu.Label>{group.label}</DropdownMenu.Label>
              {group.items.map((item) => <DropdownMenu.RadioItem key={item.value} value={item.value} disabled={item.disabled}>
                <DropdownMenu.RadioItemIndicator /><span>{item.label}</span>
              </DropdownMenu.RadioItem>)}
            </DropdownMenu.RadioGroup>)}
            {items.map((item) => (
              <DropdownMenu.Item key={item.label} disabled={item.disabled} variant={item.tone === "danger" ? "danger" : undefined} onClick={item.onClick}>
                {item.icon ? <Glyph name={item.icon} decorative /> : null}{item.label}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
