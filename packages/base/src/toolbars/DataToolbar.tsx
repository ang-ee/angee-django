import * as React from "react";
import type { ReactElement, ReactNode } from "react";
import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { titleCase } from "../lib/titleCase";
import { Button } from "../ui/button";
import { Chip } from "../ui/chip";
import { Input } from "../ui/input";
import {
  PopoverContent,
  PopoverPortal,
  PopoverPositioner,
  PopoverRoot,
  PopoverTrigger,
} from "../ui/popover";
import { Select } from "../ui/select";
import { Pager, type PagerState } from "../ui/pager";
import type {
  DataViewFavorite,
  DataViewFilter,
  DataViewGroup,
  DataViewGroupGranularity,
  DataViewKind,
  DataViewLookupOperator,
} from "../views/data-view-model";
import { dataViewGroupsEqual } from "../views/data-view-model";

export interface DataToolbarProps {
  pager: PagerState;
  view?: DataViewKind;
  group?: DataViewGroup | null;
  groupStack?: readonly DataViewGroup[];
  groupOptions?: readonly DataToolbarGroupOption[];
  filterOptions?: readonly DataToolbarFilterOption[];
  filterFields?: readonly DataToolbarFilterField[];
  customFilterChips?: readonly DataToolbarCustomFilterChip[];
  favorites?: readonly DataViewFavorite[];
  activeFilterIds?: readonly string[];
  filterText?: string;
  createLabel?: ReactNode;
  onCreate?: () => void;
  /** Extra controls rendered in the toolbar's leading slot, beside the filter. */
  actions?: ReactNode;
  /** Trailing control rendered on the right (e.g. a List/Grid layout switcher). */
  viewSwitcher?: ReactNode;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly DataViewGroup[]) => void;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  onViewChange?: (view: DataViewKind) => void;
  onCustomFilterAdd?: (filter: DataToolbarCustomFilter) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: DataViewFavorite) => void;
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

export type DataToolbarFilterFieldType =
  | "text"
  | "number"
  | "date"
  | "datetime"
  | "selection"
  | "boolean";

export type DataToolbarCustomFilterOperator =
  | DataViewLookupOperator
  | "isNotNull";

export interface DataToolbarFilterChoice {
  value: string;
  label: ReactNode;
}

export interface DataToolbarFilterField {
  id: string;
  field?: string;
  label: ReactNode;
  type?: DataToolbarFilterFieldType;
  options?: readonly DataToolbarFilterChoice[];
  operators?: readonly DataToolbarCustomFilterOperator[];
}

export interface DataToolbarCustomFilter {
  field: string;
  operator: DataToolbarCustomFilterOperator;
  value?: string | number | boolean;
  type?: DataToolbarFilterFieldType;
}

export interface DataToolbarCustomFilterChip {
  id: string;
  label: ReactNode;
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
  filterFields = [],
  customFilterChips = [],
  favorites = [],
  activeFilterIds = [],
  filterText = "",
  createLabel = "New",
  onCreate,
  actions,
  viewSwitcher,
  onFilterToggle,
  onFilterTextChange,
  onClearGroup,
  onGroupStackChange,
  onPageChange,
  onPageSizeChange,
  onViewChange,
  onCustomFilterAdd,
  onCustomFilterRemove,
  onFavoriteSave,
  onFavoriteSelect,
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
          <Glyph name="plus" className="glyph" />
          {createLabel}
        </Button>
      ) : null}
      {actions}
      <FilterPicker
        groups={groups}
        groupControls={groupControls}
        groupOptions={toolbarGroupOptions}
        activeFilters={activeFilters}
        activeFilterIds={activeFilterIds}
        filterOptions={filterOptions}
        filterFields={filterFields}
        customFilterChips={customFilterChips}
        favorites={favorites}
        filterText={filterText}
        onClearGroup={onClearGroup}
        onFilterTextChange={onFilterTextChange}
        onFilterToggle={onFilterToggle}
        onGroupStackChange={onGroupStackChange}
        onCustomFilterAdd={onCustomFilterAdd}
        onCustomFilterRemove={onCustomFilterRemove}
        onFavoriteSave={onFavoriteSave}
        onFavoriteSelect={onFavoriteSelect}
      />
      <div className="min-w-2 flex-1" />
      <Pager
        {...pager}
        subject={pagerSubject}
        unit={pagerTotalUnit}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
      />
      {view && onViewChange ? (
        <DataViewSwitcher view={view} onViewChange={onViewChange} />
      ) : null}
      {viewSwitcher}
    </section>
  );
}

function FilterPicker({
  groups,
  groupControls,
  groupOptions,
  filterOptions,
  filterFields,
  customFilterChips,
  favorites,
  activeFilters,
  activeFilterIds,
  filterText,
  onFilterTextChange,
  onFilterToggle,
  onClearGroup,
  onGroupStackChange,
  onCustomFilterAdd,
  onCustomFilterRemove,
  onFavoriteSave,
  onFavoriteSelect,
}: {
  groups: readonly DataViewGroup[];
  groupControls: boolean;
  groupOptions: readonly DataToolbarGroupOption[];
  filterOptions: readonly DataToolbarFilterOption[];
  filterFields: readonly DataToolbarFilterField[];
  customFilterChips: readonly DataToolbarCustomFilterChip[];
  favorites: readonly DataViewFavorite[];
  activeFilters: readonly DataToolbarFilterOption[];
  activeFilterIds: readonly string[];
  filterText: string;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly DataViewGroup[]) => void;
  onCustomFilterAdd?: (filter: DataToolbarCustomFilter) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: DataViewFavorite) => void;
}): ReactElement {
  const [customFilterOpen, setCustomFilterOpen] = React.useState(false);
  const [customFieldId, setCustomFieldId] = React.useState("");
  const [customOperator, setCustomOperator] =
    React.useState<DataToolbarCustomFilterOperator>("contains");
  const [customValue, setCustomValue] = React.useState("");
  const selectedCustomField =
    filterFields.find((field) => field.id === customFieldId) ?? filterFields[0];
  const effectiveCustomOperator = operatorForField(
    selectedCustomField,
    customOperator,
  );
  const [customGroupOpen, setCustomGroupOpen] = React.useState(false);
  const [customGroupId, setCustomGroupId] = React.useState("");
  const [customGroupGranularity, setCustomGroupGranularity] =
    React.useState<DataViewGroupGranularity>("day");
  const selectedCustomGroup =
    groupOptions.find((option) => option.id === customGroupId) ?? groupOptions[0];
  const [favoriteOpen, setFavoriteOpen] = React.useState(false);
  const [favoriteLabel, setFavoriteLabel] = React.useState("Saved search");

  function addCustomFilter() {
    if (!selectedCustomField || !onCustomFilterAdd) return;
    const needsValue = customFilterNeedsValue(effectiveCustomOperator);
    const value = needsValue
      ? coerceFilterValue(selectedCustomField, customValue)
      : undefined;
    if (needsValue && value === undefined) return;
    onCustomFilterAdd({
      field: selectedCustomField.field ?? selectedCustomField.id,
      operator: effectiveCustomOperator,
      ...(value !== undefined ? { value } : {}),
      ...(selectedCustomField.type ? { type: selectedCustomField.type } : {}),
    });
    setCustomValue("");
    setCustomFilterOpen(false);
  }

  function addCustomGroup() {
    if (!selectedCustomGroup || !onGroupStackChange) return;
    const group =
      selectedCustomGroup.type === "date"
        ? { ...selectedCustomGroup.group, granularity: customGroupGranularity }
        : selectedCustomGroup.group;
    onGroupStackChange([...groups, group]);
    setCustomGroupOpen(false);
  }

  function saveFavorite() {
    const label = favoriteLabel.trim();
    if (!label || !onFavoriteSave) return;
    onFavoriteSave(label);
    setFavoriteLabel("Saved search");
    setFavoriteOpen(false);
  }

  return (
    <PopoverRoot>
      <div className="inline-flex h-8 min-w-0 max-w-xl flex-1 items-center gap-1 overflow-hidden rounded-md border border-transparent bg-inset pl-2 pr-1 text-13 text-fg focus-within:border-border-focus focus-within:bg-sheet focus-within:focus-ring">
        <Glyph name="search" className="size-3.5 shrink-0 text-fg-muted" />
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
        {customFilterChips.map((chip) => (
          <FacetChip
            key={chip.id}
            label="Filter"
            value={chip.label}
            removeLabel={`Remove ${labelText(chip.label) ?? "filter"}`}
            onRemove={() => onCustomFilterRemove?.(chip.id)}
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
          <Glyph name="chevron-down" className="size-3" />
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
            <PickerColumn icon={<Glyph name="filter" className="size-3.5" />} title="Filters">
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
              <PickerButton
                active={customFilterOpen}
                muted={!customFilterOpen}
                onClick={() => setCustomFilterOpen((value) => !value)}
              >
                <Glyph name="plus" className="size-3" />
                Add custom filter
              </PickerButton>
              {customFilterOpen ? (
                <CustomFilterEditor
                  fields={filterFields}
                  field={selectedCustomField}
                  fieldId={selectedCustomField?.id ?? ""}
                  operator={effectiveCustomOperator}
                  value={customValue}
                  onField={(id) => {
                    const nextField = filterFields.find((field) =>
                      field.id === id);
                    setCustomFieldId(id);
                    setCustomOperator(defaultOperator(nextField));
                    setCustomValue("");
                  }}
                  onOperator={setCustomOperator}
                  onValue={setCustomValue}
                  onAdd={addCustomFilter}
                />
              ) : null}
            </PickerColumn>
            {groupControls ? (
              <PickerColumn
                icon={<Glyph name="sliders-horizontal" className="size-3.5" />}
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
                <PickerButton
                  active={customGroupOpen}
                  muted={!customGroupOpen}
                  onClick={() => setCustomGroupOpen((value) => !value)}
                >
                  <Glyph name="plus" className="size-3" />
                  Add custom group
                </PickerButton>
                {customGroupOpen ? (
                  <CustomGroupEditor
                    options={groupOptions}
                    option={selectedCustomGroup}
                    optionId={selectedCustomGroup?.id ?? ""}
                    granularity={customGroupGranularity}
                    onOption={(id) => {
                      const option = groupOptions.find((item) => item.id === id);
                      setCustomGroupId(id);
                      setCustomGroupGranularity(
                        option?.group.granularity ?? "day",
                      );
                    }}
                    onGranularity={setCustomGroupGranularity}
                    onAdd={addCustomGroup}
                  />
                ) : null}
              </PickerColumn>
            ) : null}
            <PickerColumn icon={<Glyph name="star" className="size-3.5" />} title="Favorites">
              <PickerButton
                active={favoriteOpen}
                muted={!favoriteOpen}
                onClick={() => setFavoriteOpen((value) => !value)}
              >
                <Glyph name="plus" className="size-3" />
                Save current search
              </PickerButton>
              {favoriteOpen ? (
                <form
                  className="mt-2 grid gap-2 rounded-md border border-border-subtle bg-sheet p-2 shadow-xs"
                  onSubmit={(event) => {
                    event.preventDefault();
                    saveFavorite();
                  }}
                >
                  <Input
                    size="sm"
                    value={favoriteLabel}
                    aria-label="Favorite name"
                    onChange={(event) =>
                      setFavoriteLabel(event.currentTarget.value)}
                  />
                  <Button
                    type="submit"
                    size="sm"
                    variant="secondary"
                    className="justify-center"
                  >
                    Save
                  </Button>
                </form>
              ) : null}
              {favorites.length === 0 ? (
                <PickerMuted>No saved searches</PickerMuted>
              ) : (
                favorites.map((favorite) => (
                  <PickerButton
                    key={favorite.id}
                    onClick={() => onFavoriteSelect?.(favorite)}
                  >
                    {favorite.label}
                  </PickerButton>
                ))
              )}
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
        <Glyph name="x" className="size-3" />
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

function CustomFilterEditor({
  fields,
  field,
  fieldId,
  operator,
  value,
  onField,
  onOperator,
  onValue,
  onAdd,
}: {
  fields: readonly DataToolbarFilterField[];
  field: DataToolbarFilterField | undefined;
  fieldId: string;
  operator: DataToolbarCustomFilterOperator;
  value: string;
  onField: (id: string) => void;
  onOperator: (operator: DataToolbarCustomFilterOperator) => void;
  onValue: (value: string) => void;
  onAdd: () => void;
}): ReactElement {
  const operators = operatorsForField(field);
  const needsValue = customFilterNeedsValue(operator);
  return (
    <div className="mt-2 grid gap-2 rounded-md border border-border-subtle bg-sheet p-2 shadow-xs">
      {fields.length === 0 ? (
        <PickerMuted>No filter fields</PickerMuted>
      ) : (
        <>
          <Select
            size="sm"
            value={fieldId}
            aria-label="Filter field"
            options={fields.map((item) => ({
              value: item.id,
              label: item.label,
            }))}
            onValueChange={onField}
          />
          <div className="flex min-w-0 gap-2">
            <Select
              size="sm"
              value={operator}
              className="min-w-0 flex-1"
              aria-label="Filter operator"
              options={operators.map((item) => ({
                value: item,
                label: FILTER_OPERATOR_LABEL[item],
              }))}
              onValueChange={(next) =>
                onOperator(next as DataToolbarCustomFilterOperator)}
            />
            {needsValue ? (
              field?.options ? (
                <Select
                  size="sm"
                  value={value}
                  className="min-w-0 flex-1"
                  aria-label="Filter value"
                  placeholder="Value"
                  options={field.options.map((option) => ({
                    value: option.value,
                    label: option.label,
                  }))}
                  onValueChange={onValue}
                />
              ) : (
                <Input
                  size="sm"
                  type={filterInputType(field)}
                  value={value}
                  placeholder="Value"
                  aria-label="Filter value"
                  className="min-w-0 flex-1"
                  onChange={(event) => onValue(event.currentTarget.value)}
                />
              )
            ) : null}
          </div>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="justify-center"
            disabled={!field || (needsValue && value.trim() === "")}
            onClick={onAdd}
          >
            Add
          </Button>
        </>
      )}
    </div>
  );
}

function CustomGroupEditor({
  options,
  option,
  optionId,
  granularity,
  onOption,
  onGranularity,
  onAdd,
}: {
  options: readonly DataToolbarGroupOption[];
  option: DataToolbarGroupOption | undefined;
  optionId: string;
  granularity: DataViewGroupGranularity;
  onOption: (id: string) => void;
  onGranularity: (granularity: DataViewGroupGranularity) => void;
  onAdd: () => void;
}): ReactElement {
  const granularities = option?.granularities ?? DEFAULT_GRANULARITIES;
  return (
    <div className="mt-2 grid gap-2 rounded-md border border-border-subtle bg-sheet p-2 shadow-xs">
      {options.length === 0 ? (
        <PickerMuted>No group fields</PickerMuted>
      ) : (
        <>
          <Select
            size="sm"
            value={optionId}
            aria-label="Group field"
            options={options.map((item) => ({
              value: item.id,
              label: item.label,
            }))}
            onValueChange={onOption}
          />
          {option?.type === "date" ? (
            <Select
              size="sm"
              value={granularity}
              aria-label="Group granularity"
              options={granularities.map((item) => ({
                value: item,
                label: titleCase(item),
              }))}
              onValueChange={(next) =>
                onGranularity(next as DataViewGroupGranularity)}
            />
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="justify-center"
            onClick={onAdd}
          >
            Add
          </Button>
        </>
      )}
    </div>
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
  const active = groups.some((group) => group.field === option.group.field);
  const granularities = option.granularities ?? DEFAULT_GRANULARITIES;
  const selectedGranularities = new Set(
    groups
      .filter((group) => group.field === option.group.field && group.granularity)
      .map((group) => group.granularity!),
  );

  return (
    <div className={cn("rounded-md", active && "bg-brand-soft")}>
      <PickerButton
        active={active}
        onClick={() => {
          if (!onGroupStackChange) return;
          if (active) {
            onGroupStackChange(
              groups.filter((group) => group.field !== option.group.field),
            );
          } else {
            onGroupStackChange([...groups, option.group]);
          }
        }}
      >
        {option.type === "date" ? (
          <Glyph name="calendar" className="size-3 text-fg-muted" />
        ) : null}
        <span className="min-w-0 flex-1 truncate">{option.label}</span>
      </PickerButton>
      {option.type === "date" ? (
        <div className="flex px-2 pb-1">
          {granularities.map((granularity) => (
            <button
              key={granularity}
              type="button"
              className={cn(
                "h-5 rounded px-1.5 text-2xs font-medium outline-none focus-visible:focus-ring",
                selectedGranularities.has(granularity)
                  ? "bg-brand text-on-brand"
                  : "text-fg-muted hover:bg-sheet",
              )}
              onClick={() => {
                const nextGroup = { ...option.group, granularity };
                const selected = groups.some((group) =>
                  dataViewGroupsEqual(group, nextGroup));
                onGroupStackChange?.(
                  selected
                    ? groups.filter((group) =>
                      !dataViewGroupsEqual(group, nextGroup))
                    : [...groups, nextGroup],
                );
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
        <Glyph name="list" className="glyph" />
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
        <Glyph name="grid-2x2" className="glyph" />
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

const FILTER_OPERATOR_LABEL = {
  exact: "is",
  inList: "is one of",
  isNull: "is empty",
  isNotNull: "is not empty",
  iExact: "is",
  contains: "contains",
  iContains: "contains",
  startsWith: "starts with",
  iStartsWith: "starts with",
  endsWith: "ends with",
  iEndsWith: "ends with",
  gt: ">",
  gte: ">=",
  lt: "<",
  lte: "<=",
} satisfies Record<DataToolbarCustomFilterOperator, string>;

const TEXT_FILTER_OPERATORS: readonly DataToolbarCustomFilterOperator[] = [
  "contains",
  "iContains",
  "iExact",
  "startsWith",
  "iStartsWith",
  "endsWith",
  "iEndsWith",
  "isNull",
  "isNotNull",
];

const COMPARISON_FILTER_OPERATORS: readonly DataToolbarCustomFilterOperator[] = [
  "exact",
  "gt",
  "gte",
  "lt",
  "lte",
  "isNull",
  "isNotNull",
];

const EXACT_FILTER_OPERATORS: readonly DataToolbarCustomFilterOperator[] = [
  "exact",
  "isNull",
  "isNotNull",
];

function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

function operatorsForField(
  field: DataToolbarFilterField | undefined,
): readonly DataToolbarCustomFilterOperator[] {
  if (field?.operators) return field.operators;
  if (
    field?.type === "number" ||
    field?.type === "date" ||
    field?.type === "datetime"
  ) {
    return COMPARISON_FILTER_OPERATORS;
  }
  if (field?.type === "selection" || field?.type === "boolean") {
    return EXACT_FILTER_OPERATORS;
  }
  return TEXT_FILTER_OPERATORS;
}

function defaultOperator(
  field: DataToolbarFilterField | undefined,
): DataToolbarCustomFilterOperator {
  return operatorsForField(field)[0] ?? "contains";
}

function operatorForField(
  field: DataToolbarFilterField | undefined,
  operator: DataToolbarCustomFilterOperator,
): DataToolbarCustomFilterOperator {
  return operatorsForField(field).includes(operator)
    ? operator
    : defaultOperator(field);
}

function customFilterNeedsValue(
  operator: DataToolbarCustomFilterOperator,
): boolean {
  return operator !== "isNull" && operator !== "isNotNull";
}

function filterInputType(field: DataToolbarFilterField | undefined): string {
  if (field?.type === "number") return "number";
  if (field?.type === "date") return "date";
  if (field?.type === "datetime") return "datetime-local";
  return "text";
}

function coerceFilterValue(
  field: DataToolbarFilterField,
  value: string,
): string | number | boolean | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (field.type === "number") {
    const number = Number(trimmed);
    return Number.isFinite(number) ? number : undefined;
  }
  if (field.type === "boolean") {
    return ["1", "true", "yes", "on"].includes(trimmed.toLowerCase());
  }
  return trimmed;
}

function labelText(value: ReactNode): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return null;
}
