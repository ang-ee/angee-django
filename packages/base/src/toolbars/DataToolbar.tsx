import type { ReactElement, ReactNode } from "react";
import { Grid2X2, List, Plus, X } from "lucide-react";
import type { UseResourceListResult } from "@angee/sdk";

import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Input } from "../ui/input";
import type { DataViewGroup, DataViewKind } from "../views/data-view-model";

export interface DataToolbarProps {
  list: UseResourceListResult;
  view: DataViewKind;
  group?: DataViewGroup | null;
  filterText?: string;
  createLabel?: ReactNode;
  onCreate?: () => void;
  onFilterTextChange?: (value: string) => void;
  onClearGroup?: () => void;
  onViewChange?: (view: DataViewKind) => void;
  className?: string;
}

export interface DataViewSwitcherProps {
  view: DataViewKind;
  onViewChange?: (view: DataViewKind) => void;
  ariaLabel?: string;
  className?: string;
}

export function DataToolbar({
  list,
  view,
  group,
  filterText = "",
  createLabel = "New",
  onCreate,
  onFilterTextChange,
  onClearGroup,
  onViewChange,
  className,
}: DataToolbarProps): ReactElement {
  const start = list.total === undefined || list.total === 0
    ? 0
    : (list.page - 1) * list.pageSize + 1;
  const end = list.total === undefined
    ? list.page * list.pageSize
    : Math.min(list.total, list.page * list.pageSize);

  return (
    <div
      className={cn(
        "flex min-h-control-h flex-wrap items-center gap-2 border-b border-border-subtle bg-sheet px-3 py-2",
        className,
      )}
    >
      {onCreate ? (
        <Button type="button" variant="primary" size="sm" onClick={onCreate}>
          <Plus className="glyph" aria-hidden />
          {createLabel}
        </Button>
      ) : null}
      {group ? (
        <Chip tone="brand" size="sm" className="gap-1">
          Group by: {groupLabel(group)}
          {onClearGroup ? (
            <button
              type="button"
              aria-label="Clear grouping"
              className="ml-0.5 rounded-full text-brand-soft-text outline-none hover:bg-on-brand-soft-hover focus-visible:focus-ring"
              onClick={onClearGroup}
            >
              <X className="size-3" aria-hidden />
            </button>
          ) : null}
        </Chip>
      ) : null}
      {onFilterTextChange ? (
        <Input
          type="search"
          value={filterText}
          placeholder="Filter..."
          aria-label="Filter records"
          className="h-7 w-48"
          onChange={(event) => onFilterTextChange(event.currentTarget.value)}
        />
      ) : null}
      <div className="min-w-2 flex-1" />
      <span className="text-13 tabular-nums text-fg-muted">
        {start}-{end}
        {list.total !== undefined ? ` / ${list.total}` : ""}
      </span>
      <DataViewSwitcher view={view} onViewChange={onViewChange} />
    </div>
  );
}

export function DataViewSwitcher({
  view,
  onViewChange,
  ariaLabel = "View switcher",
  className,
}: DataViewSwitcherProps): ReactElement {
  return (
    <div
      className={cn("flex items-center gap-1", className)}
      role="group"
      aria-label={ariaLabel}
    >
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label="List view"
        aria-pressed={view === "list"}
        active={view === "list"}
        onClick={() => onViewChange?.("list")}
      >
        <List className="glyph" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label="Board view"
        aria-pressed={view === "board"}
        active={view === "board"}
        onClick={() => onViewChange?.("board")}
      >
        <Grid2X2 className="glyph" aria-hidden />
      </Button>
    </div>
  );
}

function groupLabel(group: DataViewGroup): string {
  const field = titleCase(group.field);
  return group.granularity ? `${field} · ${titleCase(group.granularity)}` : field;
}

function titleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}
