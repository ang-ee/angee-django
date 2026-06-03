import type { ReactElement, ReactNode } from "react";
import {
  Calendar,
  ChevronDown,
  Filter,
  Grid2X2,
  List,
  Plus,
  Search,
  SlidersHorizontal,
  Star,
  X,
} from "lucide-react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { DropdownMenu } from "../ui/dropdown-menu";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
import { Pager, type PagerState } from "../ui/pager";
import type {
  DataViewFilter,
  DataViewGroup,
  DataViewGroupGranularity,
  DataViewKind,
} from "../views/data-view-model";

export interface DataToolbarProps {
  pager: PagerState;
  view?: DataViewKind;
  group?: DataViewGroup | null;
  groupStack?: readonly DataViewGroup[];
  groupOptions?: readonly DataToolbarGroupOption[];
  filterOptions?: readonly DataToolbarFilterOption[];
  visibleFields?: readonly DataToolbarVisibleField[];
  activeFilterIds?: readonly string[];
  filterText?: string;
  createLabel?: ReactNode;
  onCreate?: () => void;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly DataViewGroup[]) => void;
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  onPageChange?: (page: number) => void;
  onViewChange?: (view: DataViewKind) => void;
  pagerSubject?: string;
  pagerTotalUnit?: string;
  className?: string;
}

export interface DataToolbarFilterOption {
  id: string;
  label: ReactNode;
  chipLabel?: ReactNode;
  filter: DataViewFilter;
}

export interface DataToolbarGroupOption {
  id: string;
  label: ReactNode;
  group: DataViewGroup;
  type?: "date" | "value";
  granularities?: readonly DataViewGroupGranularity[];
}

export interface DataToolbarVisibleField {
  id: string;
  label: ReactNode;
  visible: boolean;
  disabled?: boolean;
}

export interface DataViewSwitcherProps {
  view: DataViewKind;
  onViewChange?: (view: DataViewKind) => void;
  ariaLabel?: string;
  className?: string;
}

export function DataToolbar({
  pager,
  view,
  group,
  groupStack,
  groupOptions,
  filterOptions = [],
  visibleFields = [],
  activeFilterIds = [],
  filterText = "",
  createLabel = "New",
  onCreate,
  onFilterToggle,
  onFilterTextChange,
  onClearGroup,
  onGroupStackChange,
  onVisibleFieldToggle,
  onPageChange,
  onViewChange,
  pagerSubject = "Records",
  pagerTotalUnit,
  className,
}: DataToolbarProps): ReactElement {
  const groupControls =
    groupOptions !== undefined
    || groupStack !== undefined
    || group !== undefined
    || onGroupStackChange !== undefined
    || onClearGroup !== undefined;
  const toolbarGroupOptions = groupOptions ?? [];
  const groups = groupControls ? groupStack ?? (group ? [group] : []) : [];
  const activeFilters = filterOptions.filter((option) =>
    activeFilterIds.includes(option.id),
  );
  const currentView = view ?? "list";

  return (
    <section
      aria-label="Data controls"
      className={cn(
        "flex min-h-11 items-center gap-2 border-b border-border-subtle bg-sheet px-3 py-2",
        className,
      )}
    >
      {onCreate ? (
        <Button type="button" variant="primary" size="sm" onClick={onCreate}>
          <Plus className="glyph" aria-hidden />
          {createLabel}
        </Button>
      ) : null}
      <FilterPicker
        groups={groups}
        groupControls={groupControls}
        groupOptions={toolbarGroupOptions}
        activeFilters={activeFilters}
        activeFilterIds={activeFilterIds}
        filterOptions={filterOptions}
        filterText={filterText}
        onClearGroup={onClearGroup}
        onFilterTextChange={onFilterTextChange}
        onFilterToggle={onFilterToggle}
        onGroupStackChange={onGroupStackChange}
      />
      <div className="min-w-2 flex-1" />
      <Pager
        {...pager}
        subject={pagerSubject}
        unit={pagerTotalUnit}
        onPageChange={onPageChange}
      />
      {currentView === "list" && visibleFields.length > 0 ? (
        <VisibleFieldsMenu
          fields={visibleFields}
          onToggle={onVisibleFieldToggle}
        />
      ) : null}
      {view && onViewChange ? (
        <DataViewSwitcher view={view} onViewChange={onViewChange} />
      ) : null}
    </section>
  );
}

function VisibleFieldsMenu({
  fields,
  onToggle,
}: {
  fields: readonly DataToolbarVisibleField[];
  onToggle?: (id: string, visible: boolean) => void;
}): ReactElement {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            aria-label="Visible fields"
          >
            <Glyph name="columns" />
          </Button>
        }
      />
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner sideOffset={6} align="end">
          <DropdownMenu.Content className="w-56">
            <DropdownMenu.Group>
              <DropdownMenu.Label>Visible fields</DropdownMenu.Label>
              {fields.map((field) => (
                <DropdownMenu.CheckboxItem
                  key={field.id}
                  checked={field.visible}
                  disabled={field.disabled}
                  onCheckedChange={(checked) => {
                    if (field.disabled && !checked) return;
                    onToggle?.(field.id, checked);
                  }}
                >
                  <DropdownMenu.CheckboxItemIndicator />
                  <span className="min-w-0 truncate">{field.label}</span>
                </DropdownMenu.CheckboxItem>
              ))}
            </DropdownMenu.Group>
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function FilterPicker({
  groups,
  groupControls,
  groupOptions,
  filterOptions,
  activeFilters,
  activeFilterIds,
  filterText,
  onFilterTextChange,
  onFilterToggle,
  onClearGroup,
  onGroupStackChange,
}: {
  groups: readonly DataViewGroup[];
  groupControls: boolean;
  groupOptions: readonly DataToolbarGroupOption[];
  filterOptions: readonly DataToolbarFilterOption[];
  activeFilters: readonly DataToolbarFilterOption[];
  activeFilterIds: readonly string[];
  filterText: string;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly DataViewGroup[]) => void;
}): ReactElement {
  return (
    <PopoverRoot>
      <div className="inline-flex h-8 min-w-0 max-w-xl flex-1 items-center gap-1 overflow-hidden rounded-md border border-transparent bg-inset pl-2 pr-1 text-13 text-fg focus-within:border-border-focus focus-within:bg-sheet focus-within:focus-ring">
        <Search className="size-3.5 shrink-0 text-fg-muted" aria-hidden />
        {groups.map((nextGroup, index) => (
          <FacetChip
            key={`${nextGroup.field}:${nextGroup.granularity ?? ""}`}
            label={index === 0 ? "Group by" : "Then"}
            value={groupLabel(nextGroup)}
            removeLabel={`Remove ${index === 0 ? "group" : "group level"}`}
            onRemove={() => {
              const next = groups.filter((_, groupIndex) => groupIndex !== index);
              if (next.length === 0) onClearGroup?.();
              else onGroupStackChange?.(next);
            }}
          />
        ))}
        {activeFilters.map((option) => (
          <FacetChip
            key={option.id}
            label="Filter"
            value={option.chipLabel ?? option.label}
            removeLabel={`Remove ${String(option.chipLabel ?? option.label)}`}
            onRemove={() => onFilterToggle?.(option.id)}
          />
        ))}
        <input
          type="search"
          value={filterText}
          placeholder="Filter..."
          aria-label="Filter records"
          className="h-full min-w-[7rem] flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
          onChange={(event) => onFilterTextChange?.(event.currentTarget.value)}
        />
        <PopoverTrigger
          className="grid size-6 shrink-0 place-content-center rounded text-fg-muted outline-none transition-colors hover:bg-sheet hover:text-fg focus-visible:focus-ring"
          aria-label={
            groupControls ? "Filter, group, favorites" : "Filter and favorites"
          }
        >
          <ChevronDown className="size-3" aria-hidden />
        </PopoverTrigger>
      </div>
      <PopoverPortal>
        <PopoverPositioner sideOffset={6} align="start">
          <PopoverContent
            className={cn(
              "grid max-w-[calc(100vw-2rem)]",
              groupControls ? "w-[45rem] grid-cols-3" : "w-[30rem] grid-cols-2",
            )}
          >
            <PickerColumn icon={<Filter className="size-3.5" />} title="Filters">
              {filterOptions.length === 0 ? (
                <PickerMuted>No filters</PickerMuted>
              ) : (
                filterOptions.map((option) => (
                  <PickerButton
                    key={option.id}
                    active={activeFilterIds.includes(option.id)}
                    onClick={() => onFilterToggle?.(option.id)}
                  >
                    {option.label}
                  </PickerButton>
                ))
              )}
              <PickerDivider />
              <PickerButton muted>
                <Plus className="size-3" aria-hidden />
                Add custom filter
              </PickerButton>
            </PickerColumn>
            {groupControls ? (
              <PickerColumn
                icon={<SlidersHorizontal className="size-3.5" />}
                title="Group by"
              >
                {groupOptions.map((option) => (
                  <GroupOptionButton
                    key={option.id}
                    option={option}
                    groups={groups}
                    onGroupStackChange={onGroupStackChange}
                  />
                ))}
                <PickerDivider />
                <PickerButton muted>
                  <Plus className="size-3" aria-hidden />
                  Add custom group
                </PickerButton>
              </PickerColumn>
            ) : null}
            <PickerColumn icon={<Star className="size-3.5" />} title="Favorites">
              <PickerButton muted>
                <Plus className="size-3" aria-hidden />
                Save current search
              </PickerButton>
            </PickerColumn>
          </PopoverContent>
        </PopoverPositioner>
      </PopoverPortal>
    </PopoverRoot>
  );
}

function FacetChip({
  label,
  value,
  removeLabel,
  onRemove,
}: {
  label: ReactNode;
  value: ReactNode;
  removeLabel: string;
  onRemove: () => void;
}): ReactElement {
  return (
    <Chip tone="brand" size="sm" className="max-w-52 gap-1">
      <span className="shrink-0">{label}:</span>
      <span className="truncate">{value}</span>
      <button
        type="button"
        aria-label={removeLabel}
        className="ml-0.5 rounded-full text-brand-soft-text outline-none hover:bg-on-brand-soft-hover focus-visible:focus-ring"
        onClick={onRemove}
      >
        <X className="size-3" aria-hidden />
      </button>
    </Chip>
  );
}

function PickerColumn({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: ReactNode;
  children: ReactNode;
}): ReactElement {
  return (
    <section className="min-w-0 border-r border-border-subtle p-3 last:border-r-0">
      <h3 className="mb-2 flex items-center gap-2 text-13 font-semibold text-fg">
        <span className="text-brand-soft-text">{icon}</span>
        {title}
      </h3>
      <div className="grid gap-1">{children}</div>
    </section>
  );
}

function PickerButton({
  active = false,
  muted = false,
  children,
  onClick,
}: {
  active?: boolean;
  muted?: boolean;
  children: ReactNode;
  onClick?: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      className={cn(
        "flex h-7 min-w-0 items-center gap-2 rounded-md px-2 text-left text-13 outline-none transition-colors focus-visible:focus-ring",
        active
          ? "bg-brand-soft font-medium text-brand-soft-text"
          : muted
            ? "text-fg-muted hover:bg-inset hover:text-fg"
            : "text-fg hover:bg-inset",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function GroupOptionButton({
  option,
  groups,
  onGroupStackChange,
}: {
  option: DataToolbarGroupOption;
  groups: readonly DataViewGroup[];
  onGroupStackChange?: (groups: readonly DataViewGroup[]) => void;
}): ReactElement {
  const activeIndex = groups.findIndex((group) => group.field === option.group.field);
  const active = activeIndex >= 0;
  const granularities = option.granularities ?? DEFAULT_GRANULARITIES;
  const selectedGranularity =
    activeIndex >= 0
      ? groups[activeIndex]?.granularity
      : option.group.granularity;

  return (
    <div className={cn("rounded-md", active && "bg-brand-soft")}>
      <PickerButton
        active={active}
        onClick={() => {
          if (!onGroupStackChange) return;
          if (active) {
            onGroupStackChange(
              groups.filter((_, index) => index !== activeIndex),
            );
          } else {
            onGroupStackChange([...groups, option.group]);
          }
        }}
      >
        {option.type === "date" ? (
          <Calendar className="size-3 text-fg-muted" aria-hidden />
        ) : null}
        <span className="min-w-0 flex-1 truncate">{option.label}</span>
      </PickerButton>
      {option.type === "date" && active ? (
        <div className="flex px-2 pb-1">
          {granularities.map((granularity) => (
            <button
              key={granularity}
              type="button"
              className={cn(
                "h-5 rounded px-1.5 text-2xs font-medium outline-none focus-visible:focus-ring",
                selectedGranularity === granularity
                  ? "bg-brand text-on-brand"
                  : "text-fg-muted hover:bg-sheet",
              )}
              onClick={() => {
                const next = groups.map((group, index) =>
                  index === activeIndex ? { ...group, granularity } : group,
                );
                onGroupStackChange?.(next);
              }}
            >
              {titleCase(granularity)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PickerDivider(): ReactElement {
  return <div className="my-1 border-t border-border-subtle" />;
}

function PickerMuted({ children }: { children: ReactNode }): ReactElement {
  return <p className="px-2 py-1 text-13 text-fg-muted">{children}</p>;
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
  const field = groupFieldLabel(group.field);
  return group.granularity ? `${field} · ${titleCase(group.granularity)}` : field;
}

const DEFAULT_GRANULARITIES: readonly DataViewGroupGranularity[] = [
  "year",
  "quarter",
  "month",
  "week",
  "day",
];

function titleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}
