import * as React from "react";
import type { ReactElement, ReactNode } from "react";
import type { FilterValue } from "@angee/metadata";
import { useDebouncedText } from "../lib/use-debounced-text";
import { useContainerQuery } from "../lib/use-container-query";
import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { titleCase } from "../lib/titleCase";
import { Button } from "../ui/button";
import { RemovableChip } from "../ui/chip";
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
import { textRoleVariants } from "../ui/text";
import {
  SegmentedControl,
  type SegmentedControlOption,
} from "../ui/toggle-group";
import type {
  CalendarViewMode,
  ResourceViewFavorite,
  ResourceViewFilter,
  ResourceViewGroup,
  ResourceViewGroupGranularity,
  ResourceViewKind,
  ResourceViewLookupOperator,
} from "../views/resource/resource-view-model";
import {
  resourceViewGroupsEqual,
  resourceViewKindCapabilities,
} from "../views/resource/resource-view-model";
import { groupFieldLabel } from "../views/resource/resource-view-list-body";
import {
  filterOperatorLabel,
  labelText,
} from "../views/resource/resource-view-utils";

export interface ResourceToolbarChrome {
  viewSwitcher?: boolean;
  pager?: boolean;
}

export interface ResourceToolbarProps {
  pager: PagerState;
  chrome?: ResourceToolbarChrome;
  maxGroupDepth?: number;
  view?: ResourceViewKind;
  group?: ResourceViewGroup | null;
  groupStack?: readonly ResourceViewGroup[];
  /** Curated grouping shortcuts shown directly in the Group by menu. */
  groupOptions?: readonly ResourceToolbarGroupOption[];
  /** Complete supported grouping catalog for the custom group editor. Falls
   * back to `groupOptions` for standalone callers that omit it. */
  customGroupOptions?: readonly ResourceToolbarGroupOption[];
  filterOptions?: readonly ResourceToolbarFilterOption[];
  customFilterFields?: readonly ResourceToolbarFilterField[];
  customFilterChips?: readonly ResourceToolbarCustomFilterChip[];
  favorites?: readonly ResourceViewFavorite[];
  /** Present filter-option or shipped-preset ids and facets in a compact row. */
  filterRow?: { quickFilterIds?: readonly string[]; facetIds?: readonly string[] };
  facetLabels?: Readonly<Record<string, ReactNode>>;
  activeFilterIds?: readonly string[];
  activeFavoriteIds?: readonly string[];
  filterText?: string;
  createLabel?: ReactNode;
  onCreate?: () => void;
  /** Extra controls rendered in the toolbar's leading slot, beside the filter. */
  actions?: ReactNode;
  /** Cross-resource utilities rendered after the query controls and before the
   * pager. Global collection actions such as Share compose here. */
  utilityActions?: ReactNode;
  /** View-contributed controls (period nav + mode switch + title) for the active
   * kind — the calendar contributes these; list/board contribute none. */
  viewControls?: ResourceToolbarViewControls;
  /** The kinds the switcher offers — derived from the page's declared kinds
   * (defaults to list + board). */
  availableViews?: readonly ResourceViewKind[];
  /** Trailing control rendered on the right (e.g. a List/Grid layout switcher). */
  viewSwitcher?: ReactNode;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onFacetChange?: (field: string, optionId: string | null) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pagerPageSizeOptions?: readonly number[];
  pagerMaxPageSize?: number;
  onViewChange?: (view: ResourceViewKind) => void;
  onCustomFilterAdd?: (filter: ResourceToolbarCustomFilter) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
  onFavoriteToggle?: (favorite: ResourceViewFavorite) => void;
  onFavoriteRename?: (id: string, label: string) => void;
  onFavoritePin?: (id: string, pinned: boolean) => void;
  /** Clear filter, sorting and grouping state together. */
  onQueryReset?: () => void;
  /** Explicit baseline comparison; standalone toolbars infer this from active controls. */
  queryDirty?: boolean;
  pagerSubject?: string;
  pagerTotalUnit?: string;
  className?: string;
  /** Allow controls to wrap when the containing pane is narrow. */
  wrap?: boolean;
}

export interface ResourceToolbarFilterOption {
  id: string;
  label: ReactNode;
  chipLabel?: ReactNode;
  /** Compound preset; its individual value chips describe the active predicates. */
  preset?: boolean;
  filter: ResourceViewFilter;
  group?: string;
}

/** The typed view-controls seam a kind contributes: mode switch + period nav +
 * current-period title, rendered with Angee primitives. */
export interface ResourceToolbarViewControls {
  /** The active window mode. */
  mode: CalendarViewMode;
  /** The mode-switch options (labelled by the contributing kind). */
  modeOptions: readonly SegmentedControlOption<CalendarViewMode>[];
  onModeChange: (mode: CalendarViewMode) => void;
  /** The current-period title (derived from mode + period, no imperative API). */
  title: ReactNode;
  onPrev: () => void;
  onToday: () => void;
  onNext: () => void;
}

export interface ResourceToolbarGroupOption {
  id: string;
  label: ReactNode;
  group: ResourceViewGroup;
  type?: "date" | "value";
  granularities?: readonly ResourceViewGroupGranularity[];
}

export type ResourceToolbarFilterFieldType =
  | "text"
  | "number"
  | "date"
  | "datetime"
  | "selection"
  | "boolean";

export type ResourceToolbarCustomFilterOperator =
  | ResourceViewLookupOperator
  | "isNotNull";

export interface ResourceToolbarFilterChoice {
  value: string;
  label: ReactNode;
}

export interface ResourceToolbarFilterField {
  id: string;
  field?: string;
  label: ReactNode;
  group?: string;
  type?: ResourceToolbarFilterFieldType;
  options?: readonly ResourceToolbarFilterChoice[];
  operators?: readonly ResourceToolbarCustomFilterOperator[];
  /** Declared value picker, for example an authorized relation search. */
  renderValue?: (props: {
    value: string;
    onValueChange: (value: string) => void;
  }) => ReactNode;
}

export interface ResourceToolbarCustomFilter {
  field: string;
  operator: ResourceToolbarCustomFilterOperator;
  value?: FilterValue;
  type?: ResourceToolbarFilterFieldType;
}

export interface ResourceToolbarCustomFilterChip {
  id: string;
  label: ReactNode;
}

export interface ResourceViewSwitcherProps<TView extends string = ResourceViewKind> {
  view: TView;
  onViewChange?: (view: TView) => void;
  mode?: "resource" | "layout";
  /** The resource-mode kinds to offer; defaults to list + board. */
  kinds?: readonly ResourceViewKind[];
  ariaLabel?: string;
  className?: string;
  favorites?: readonly ResourceViewFavorite[];
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
}

/** Per-kind switcher chrome — the label key + glyph, keyed by kind. */
const RESOURCE_VIEW_KIND_SWITCHER: Record<
  ResourceViewKind,
  { labelKey: string; icon: string }
> = {
  list: { labelKey: "resourceToolbar.listView", icon: "list" },
  board: { labelKey: "resourceToolbar.boardView", icon: "grid-2x2" },
  calendar: { labelKey: "resourceToolbar.calendarView", icon: "calendar" },
  gantt: { labelKey: "resourceToolbar.ganttView", icon: "chart-gantt" },
  dashboard: { labelKey: "resourceToolbar.dashboardView", icon: "chart-no-axes-combined" },
};

const DEFAULT_SWITCHER_KINDS: readonly ResourceViewKind[] = ["list", "board"];
const PRIMARY_GROUP_GRANULARITIES = new Set<ResourceViewGroupGranularity>([
  "year",
  "quarter",
  "month",
  "week",
  "day",
]);

export function ResourceToolbar({
  pager,
  chrome,
  maxGroupDepth,
  view,
  group,
  groupStack,
  groupOptions,
  customGroupOptions,
  filterOptions = [],
  customFilterFields = [],
  customFilterChips = [],
  favorites = [],
  filterRow,
  facetLabels,
  activeFilterIds = [],
  activeFavoriteIds = [],
  filterText = "",
  createLabel,
  onCreate,
  actions,
  utilityActions,
  viewControls,
  availableViews,
  viewSwitcher,
  onFilterToggle,
  onFacetChange,
  onFilterTextChange,
  onClearGroup,
  onGroupStackChange: changeGroupStack,
  onPageChange,
  onPageSizeChange,
  pagerPageSizeOptions,
  pagerMaxPageSize,
  onViewChange,
  onCustomFilterAdd,
  onCustomFilterRemove,
  onFavoriteSave,
  onFavoriteSelect,
  onFavoriteToggle,
  onFavoriteRename,
  onFavoritePin,
  onQueryReset,
  queryDirty,
  pagerSubject,
  pagerTotalUnit,
  className,
  wrap = false,
}: ResourceToolbarProps): ReactElement {
  const t = useUiT();
  const onGroupStackChange = React.useMemo(
    () =>
      changeGroupStack
        ? (groups: readonly ResourceViewGroup[]) =>
            changeGroupStack(
              maxGroupDepth === undefined
                ? groups
                : groups.slice(-Math.max(1, maxGroupDepth)),
            )
        : undefined,
    [changeGroupStack, maxGroupDepth],
  );
  const resolvedCreateLabel = createLabel ?? t("resourceToolbar.create");
  // The active kind's applicability gates the data controls: the calendar shows
  // none of filter/pager/group-by; a surface that names no kind keeps them all.
  const capabilities = resourceViewKindCapabilities(view);
  const groupControls =
    capabilities.grouping &&
    (groupOptions !== undefined ||
      customGroupOptions !== undefined ||
      groupStack !== undefined ||
      group !== undefined ||
      onGroupStackChange !== undefined ||
      onClearGroup !== undefined);
  const toolbarGroupOptions = groupOptions ?? [];
  const toolbarCustomGroupOptions = customGroupOptions ?? toolbarGroupOptions;
  const groups = groupControls ? groupStack ?? (group ? [group] : []) : [];
  const activeFilters = filterOptions.filter(
    (option) => activeFilterIds.includes(option.id) && !option.preset,
  );
  const clearable = queryDirty ?? (
    activeFilterIds.length > 0 || activeFavoriteIds.length > 0
    || customFilterChips.length > 0 || Boolean(filterText) || groups.length > 0
  );
  return (
    <section
      aria-label={t("resourceToolbar.controls")}
      className={cn(
        "resource-toolbar min-h-11 border-b border-border-subtle bg-sheet px-3 py-2",
        filterRow && "resource-toolbar-filter-row",
        wrap && "resource-toolbar-wrap",
        className,
      )}
    >
      <div className="resource-toolbar-actions">
        {onCreate ? (
          <Button type="button" variant="primary" size="sm" onClick={onCreate}>
            <Glyph name="plus" className="glyph" />
            {resolvedCreateLabel}
          </Button>
        ) : null}
        {actions}
        {viewControls ? <ResourceViewControls {...viewControls} /> : null}
      </div>
      {capabilities.filter ? (
        <div
          className="resource-toolbar-query flex min-w-0 flex-wrap items-center gap-2"
        >
          {filterRow ? (
            <FilterRow
              favorites={favorites}
              quickFilterIds={filterRow.quickFilterIds ?? []}
              facetIds={filterRow.facetIds ?? []}
              facetLabels={facetLabels}
              filterOptions={filterOptions}
              customFilterFields={customFilterFields}
              activeFilterIds={activeFilterIds}
              activeFavoriteIds={activeFavoriteIds}
              queryDirty={clearable}
              onFilterToggle={onFilterToggle}
              onFacetChange={onFacetChange}
              onFavoriteToggle={onFavoriteToggle}
              onQueryReset={onQueryReset}
            />
          ) : null}
          <FilterPicker
            compact={Boolean(filterRow)}
            activeFilters={activeFilters}
            activeFilterIds={activeFilterIds}
            filterOptions={filterOptions}
            customFilterFields={customFilterFields}
            customFilterChips={customFilterChips}
            favorites={favorites}
            filterText={filterText}
            onFilterTextChange={onFilterTextChange}
            onFilterToggle={onFilterToggle}
            onCustomFilterAdd={onCustomFilterAdd}
            onCustomFilterRemove={onCustomFilterRemove}
            onFavoriteSave={onFavoriteSave}
            onFavoriteSelect={onFavoriteSelect}
            onFavoriteRename={onFavoriteRename}
            onFavoritePin={onFavoritePin}
          />
          {groupControls ? (
            <GroupByControl
              groups={groups}
              groupOptions={toolbarGroupOptions}
              customGroupOptions={toolbarCustomGroupOptions}
              onGroupStackChange={onGroupStackChange}
              onClearGroup={onClearGroup}
            />
          ) : null}
        </div>
      ) : null}
      <div className="resource-toolbar-utilities">
        {utilityActions}
        {!filterRow && clearable && onQueryReset ? (
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            aria-label={t("resourceToolbar.clearQuery")}
            onClick={onQueryReset}
          >
            <Glyph name="undo-2" fallbackName="x" />
          </Button>
        ) : null}
        {capabilities.pagination && chrome?.pager !== false ? (
          <Pager
            {...pager}
            subject={pagerSubject}
            unit={pagerTotalUnit}
            pageSizeOptions={pagerPageSizeOptions}
            maxPageSize={pagerMaxPageSize}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        ) : null}
        {view && onViewChange && chrome?.viewSwitcher !== false ? (
          <ResourceViewSwitcher
            view={view}
            kinds={availableViews}
            favorites={favorites}
            onFavoriteSelect={onFavoriteSelect}
            onViewChange={onViewChange}
          />
        ) : null}
        {chrome?.viewSwitcher !== false ? viewSwitcher : null}
      </div>
    </section>
  );
}

/** The kind-contributed view controls: period nav + current-period title + mode
 * switch, rendered with Angee primitives. */
function ResourceViewControls({
  mode,
  modeOptions,
  onModeChange,
  title,
  onPrev,
  onToday,
  onNext,
}: ResourceToolbarViewControls): ReactElement {
  const t = useUiT();
  return (
    <div className="flex min-w-0 items-center gap-2">
      <div className="flex items-center gap-0.5">
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label={t("resourceToolbar.periodPrev")}
          onClick={onPrev}
        >
          <Glyph name="chevron-left" className="glyph" />
        </Button>
        <Button type="button" variant="secondary" size="sm" onClick={onToday}>
          {t("resourceToolbar.today")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label={t("resourceToolbar.periodNext")}
          onClick={onNext}
        >
          <Glyph name="chevron-right" className="glyph" />
        </Button>
      </div>
      <span className={cn(textRoleVariants({ role: "title" }), "min-w-0 truncate")}>
        {title}
      </span>
      <SegmentedControl
        size="sm"
        options={modeOptions}
        value={mode}
        onValueChange={onModeChange}
        aria-label={t("resourceToolbar.periodMode")}
      />
    </div>
  );
}

function FilterRow({
  favorites, quickFilterIds, facetIds, facetLabels, filterOptions, customFilterFields,
  activeFilterIds, activeFavoriteIds, queryDirty,
  onFilterToggle, onFacetChange, onFavoriteToggle, onQueryReset,
}: {
  favorites: readonly ResourceViewFavorite[];
  quickFilterIds: readonly string[];
  facetIds: readonly string[];
  facetLabels?: Readonly<Record<string, ReactNode>>;
  filterOptions: readonly ResourceToolbarFilterOption[];
  customFilterFields: readonly ResourceToolbarFilterField[];
  activeFilterIds: readonly string[];
  activeFavoriteIds: readonly string[];
  queryDirty: boolean;
  onFilterToggle?: (id: string) => void;
  onFacetChange?: (field: string, optionId: string | null) => void;
  onFavoriteToggle?: (favorite: ResourceViewFavorite) => void;
  onQueryReset?: () => void;
}): ReactElement {
  const t = useUiT();
  const pinnedFavorites = favorites.filter((favorite) => favorite.pinned);
  const quickFilters = quickFilterIds.flatMap((id) => {
    if (pinnedFavorites.some((favorite) => favorite.id === id)) return [];
    const option = filterOptions.find((candidate) => candidate.id === id);
    if (option) return [{ id, label: option.label, active: activeFilterIds.includes(id), onClick: () => onFilterToggle?.(id) }];
    const favorite = favorites.find((candidate) => candidate.id === id);
    return favorite ? [{ id, label: favorite.label, active: activeFavoriteIds.includes(id), onClick: () => onFavoriteToggle?.(favorite) }] : [];
  });
  return <div className="flex min-w-0 flex-wrap items-center gap-1.5" aria-label={t("resourceToolbar.filters")}>
    {pinnedFavorites.map((favorite) => <Button key={favorite.id}
      type="button" size="sm" variant="ghost" active={activeFavoriteIds.includes(favorite.id)}
      aria-pressed={activeFavoriteIds.includes(favorite.id)} onClick={() => onFavoriteToggle?.(favorite)}>
      {favorite.label}</Button>)}
    {quickFilters.map((option) => <Button key={option.id} type="button" size="sm" variant="ghost"
      active={option.active} aria-pressed={option.active}
      onClick={option.onClick}>{option.label}</Button>)}
    {facetIds.map((field) => {
      const choices = filterOptions.filter((option) => option.id.startsWith(`${field}:`));
      const descriptor = customFilterFields.find((option) => (option.field ?? option.id) === field);
      const selected = choices.find((choice) => activeFilterIds.includes(choice.id));
      if (choices.length === 0) return null;
      const label = facetLabels?.[field] ?? descriptor?.label ?? titleCase(field);
      return <Select key={field} size="sm" aria-label={labelText(label) ?? titleCase(field)}
        value={selected?.id ?? ""} placeholder={label}
        options={[{ value: "", label }, ...choices.map((choice) => ({ value: choice.id, label: choice.label }))]}
        onValueChange={(value) => onFacetChange?.(field, value || null)} />;
    })}
    {queryDirty && onQueryReset ? <Button type="button" size="sm" variant="ghost"
      onClick={onQueryReset}>{t("resourceToolbar.clear")}</Button> : null}
  </div>;
}

function GroupByControl({ groups, groupOptions, customGroupOptions, onGroupStackChange, onClearGroup }: {
  groups: readonly ResourceViewGroup[];
  groupOptions: readonly ResourceToolbarGroupOption[];
  customGroupOptions: readonly ResourceToolbarGroupOption[];
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
  onClearGroup?: () => void;
}): ReactElement {
  const t = useUiT();
  const [customOpen, setCustomOpen] = React.useState(false);
  const [customId, setCustomId] = React.useState("");
  const [granularity, setGranularity] = React.useState<ResourceViewGroupGranularity>("day");
  const selected = customGroupOptions.find((option) => option.id === customId) ?? customGroupOptions[0];
  return <PopoverRoot><PopoverTrigger className="inline-flex h-8 items-center gap-1 rounded-6 px-2 text-xs text-fg-muted outline-none hover:bg-inset focus-visible:focus-ring"
    aria-label={t("resourceToolbar.groupBy")}>
    <Glyph name="sliders-horizontal" className="size-3.5" />
    {groups.length > 0
      ? t("resourceToolbar.groupByActive", { groups: groups.map((group) => resourceViewGroupLabel(group, [...groupOptions, ...customGroupOptions])).join(", ") })
      : t("resourceToolbar.groupBy")}
    <Glyph name="chevron-down" className="size-3" />
  </PopoverTrigger><PopoverPortal><PopoverPositioner sideOffset={6} align="start"><PopoverContent className="grid w-60 gap-1 p-2">
    {groupOptions.map((option) => <GroupOptionButton key={option.id} option={option} groups={groups}
      onGroupStackChange={onGroupStackChange} />)}
    {groups.length > 0 ? <PickerButton onClick={() => onClearGroup ? onClearGroup() : onGroupStackChange?.([])}>{t("resourceToolbar.clearGroup")}</PickerButton> : null}
    <PickerButton active={customOpen} onClick={() => setCustomOpen((open) => !open)}>
      <Glyph name="plus" className="size-3" />{t("resourceToolbar.addCustomGroup")}</PickerButton>
    {customOpen ? <CustomGroupEditor options={customGroupOptions} option={selected} optionId={selected?.id ?? ""}
      granularity={groupGranularity(selected, granularity)} onOption={setCustomId} onGranularity={setGranularity}
      onAdd={() => { if (selected && onGroupStackChange) {
        const group = selected.type === "date" ? { ...selected.group, granularity: groupGranularity(selected, granularity) } : selected.group;
        if (!groups.some((entry) => resourceViewGroupsEqual(entry, group))) onGroupStackChange([...groups, group]);
        setCustomOpen(false);
      } }} /> : null}
  </PopoverContent></PopoverPositioner></PopoverPortal></PopoverRoot>;
}

function FilterPicker({
  compact,
  filterOptions,
  customFilterFields,
  customFilterChips,
  favorites,
  activeFilters,
  activeFilterIds,
  filterText,
  onFilterTextChange,
  onFilterToggle,
  onCustomFilterAdd,
  onCustomFilterRemove,
  onFavoriteSave,
  onFavoriteSelect,
  onFavoriteRename,
  onFavoritePin,
}: {
  compact: boolean;
  filterOptions: readonly ResourceToolbarFilterOption[];
  customFilterFields: readonly ResourceToolbarFilterField[];
  customFilterChips: readonly ResourceToolbarCustomFilterChip[];
  favorites: readonly ResourceViewFavorite[];
  activeFilters: readonly ResourceToolbarFilterOption[];
  activeFilterIds: readonly string[];
  filterText: string;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onCustomFilterAdd?: (filter: ResourceToolbarCustomFilter) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
  onFavoriteRename?: (id: string, label: string) => void;
  onFavoritePin?: (id: string, pinned: boolean) => void;
}): ReactElement {
  const t = useUiT();
  const [pickerHostRef, roomyPicker] = useContainerQuery(640);
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const defaultFavoriteLabel = t("resourceToolbar.savedSearch");
  const [customFilterOpen, setCustomFilterOpen] = React.useState(false);
  const groupedFilters = React.useMemo(() => {
    const sections = new Map<string, ResourceToolbarFilterOption[]>();
    for (const option of filterOptions) {
      const label = option.group ?? "";
      const choices = sections.get(label) ?? [];
      choices.push(option);
      sections.set(label, choices);
    }
    return [...sections];
  }, [filterOptions]);
  const [customFieldId, setCustomFieldId] = React.useState("");
  const [customOperator, setCustomOperator] =
    React.useState<ResourceToolbarCustomFilterOperator>("contains");
  const [customValue, setCustomValue] = React.useState("");
  const [customValueError, setCustomValueError] = React.useState<string>();
  const customEditorRef = React.useRef<HTMLDivElement | null>(null);
  const selectedCustomField =
    customFilterFields.find((field) => field.id === customFieldId) ??
    customFilterFields[0];
  const effectiveCustomOperator = operatorForField(
    selectedCustomField,
    customOperator,
  );
  const [favoriteOpen, setFavoriteOpen] = React.useState(false);
  const [editingFavoriteId, setEditingFavoriteId] = React.useState<string | null>(null);
  const [favoriteLabel, setFavoriteLabel] =
    React.useState(defaultFavoriteLabel);
  const favoritesEnabled = onFavoriteSave !== undefined || favorites.length > 0;
  const {
    draft: draftFilterText,
    setDraft: setDraftFilterText,
    commit: commitFilterText,
  } = useDebouncedText(filterText, onFilterTextChange);

  function addCustomFilter() {
    if (!selectedCustomField || !onCustomFilterAdd) return;
    const needsValue = customFilterNeedsValue(effectiveCustomOperator);
    let value: FilterValue | undefined;
    try {
      value = needsValue
        ? coerceFilterValue(
            selectedCustomField,
            customValue,
            effectiveCustomOperator,
          )
        : undefined;
    } catch {
      setCustomValueError(t("resourceToolbar.invalidJson"));
      return;
    }
    if (needsValue && value === undefined) {
      setCustomValueError(t("resourceToolbar.invalidValue"));
      return;
    }
    setCustomValueError(undefined);
    onCustomFilterAdd({
      field: selectedCustomField.field ?? selectedCustomField.id,
      operator: effectiveCustomOperator,
      ...(value !== undefined ? { value } : {}),
      ...(selectedCustomField.type ? { type: selectedCustomField.type } : {}),
    });
    setCustomValue("");
    globalThis.requestAnimationFrame(() => {
      const target = customEditorRef.current?.querySelector<HTMLElement>(
        "input:not([disabled]), button:not([disabled]), [tabindex='0']",
      );
      target?.focus();
    });
  }

  function saveFavorite() {
    const label = favoriteLabel.trim();
    if (!label || !onFavoriteSave) return;
    onFavoriteSave(label);
    setFavoriteLabel(defaultFavoriteLabel);
    setFavoriteOpen(false);
  }

  const searchInput = onFilterTextChange ? (
    <input
      type="search"
      value={draftFilterText}
      placeholder={t("resourceToolbar.filterPlaceholder")}
      aria-label={t("resourceToolbar.filterRecords")}
      className={cn(
        "min-w-[7rem] border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted",
        compact ? "h-8 w-full rounded-6 bg-inset px-2" : "h-full flex-1",
      )}
      onBlur={(event) => {
        commitFilterText(event.currentTarget.value);
        commitFilterText.flush();
      }}
      onChange={(event) => {
        const value = event.currentTarget.value;
        setDraftFilterText(value);
        commitFilterText(value);
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          commitFilterText(event.currentTarget.value);
          commitFilterText.flush();
        }
      }}
    />
  ) : null;

  return (
    <PopoverRoot open={pickerOpen} onOpenChange={setPickerOpen}>
      <div
        ref={pickerHostRef}
        className={cn(
          "flex h-8 min-w-0 items-center gap-1 rounded-6 border border-transparent bg-inset pl-2 pr-1 text-13 text-fg focus-within:border-border-focus focus-within:bg-sheet focus-within:focus-ring",
          compact ? "w-8 justify-center p-0" : "flex-1",
        )}
      >
        {!compact ? <Glyph name="search" className="size-3.5 shrink-0 text-fg-muted" /> : null}
        {!compact ? activeFilters.slice(0, roomyPicker ? undefined : 1).map((option) => (
          <FacetChip
            key={option.id}
            label={t("resourceToolbar.filter")}
            value={option.chipLabel ?? option.label}
            removeLabel={String(option.chipLabel ?? option.label)}
            onRemove={() => onFilterToggle?.(option.id)}
          />
        )) : null}
        {!compact ? customFilterChips.slice(0, roomyPicker ? undefined : Math.max(0, 1 - activeFilters.length)).map((chip) => (
          <FacetChip
            key={chip.id}
            label={t("resourceToolbar.filter")}
            value={chip.label}
            removeLabel={
              labelText(chip.label) ?? t("resourceToolbar.filterFallback")
            }
            onRemove={() => onCustomFilterRemove?.(chip.id)}
          />
        )) : null}
        {!compact && !roomyPicker && activeFilters.length + customFilterChips.length > 1 ? (
          <button
            type="button"
            className="h-6 shrink-0 rounded-6 bg-brand-soft px-2 text-xs font-medium text-brand-soft-text outline-none focus-visible:focus-ring"
            onClick={() => setPickerOpen(true)}
          >
            +{activeFilters.length + customFilterChips.length - 1}
          </button>
        ) : null}
        {!compact ? searchInput : null}
        <PopoverTrigger
          className="grid size-6 shrink-0 place-content-center rounded-6 text-fg-muted outline-none transition-colors hover:bg-sheet hover:text-fg focus-visible:focus-ring"
          aria-label={
            t(favoritesEnabled ? "resourceToolbar.filterAndFavorites" : "resourceToolbar.filter")
          }
        >
          <Glyph name={compact ? "filter" : "chevron-down"} className="size-3.5" />
        </PopoverTrigger>
      </div>
      <PopoverPortal>
        <PopoverPositioner sideOffset={6} align="start">
          <PopoverContent
            className={cn(
              "grid max-h-[min(36rem,calc(100dvh-5rem))] max-w-[calc(100vw-1rem)] overflow-y-auto overscroll-contain",
              roomyPicker
                ? favoritesEnabled ? "w-[30rem] grid-cols-2" : "w-[18rem] grid-cols-1"
                : "w-[min(22rem,calc(100vw-1rem))] grid-cols-1",
            )}
          >
            <PickerColumn
              stacked={!roomyPicker}
              icon={<Glyph name="filter" className="size-3.5" />}
              title={t("resourceToolbar.filters")}
            >
              {compact ? searchInput : null}
              {activeFilters.length > 0 || customFilterChips.length > 0 ? (
                <div className="mb-2 flex min-w-0 flex-wrap gap-1 border-b border-border-subtle pb-2">
                  {activeFilters.map((option) => (
                    <RemovableChip
                      key={option.id}
                      tone="brand"
                      size="sm"
                      removeLabel={String(option.chipLabel ?? option.label)}
                      onRemove={() => onFilterToggle?.(option.id)}
                    >
                      {option.chipLabel ?? option.label}
                    </RemovableChip>
                  ))}
                  {customFilterChips.map((chip) => (
                    <RemovableChip
                      key={chip.id}
                      tone="brand"
                      size="sm"
                      removeLabel={labelText(chip.label) ?? t("resourceToolbar.filterFallback")}
                      onRemove={() => onCustomFilterRemove?.(chip.id)}
                    >
                      {chip.label}
                    </RemovableChip>
                  ))}
                </div>
              ) : null}
              {filterOptions.length === 0 ? (
                <PickerMuted>{t("resourceToolbar.noFilters")}</PickerMuted>
              ) : (
                groupedFilters.map(([label, choices]) => (
                  <section
                    key={label}
                    aria-label={label || undefined}
                    className="grid gap-1"
                  >
                    {label ? (
                      <div className="px-2 pt-2 text-2xs font-semibold text-fg-muted">
                        {label}
                      </div>
                    ) : null}
                    {choices.map((option) => (
                      <PickerButton
                        key={option.id}
                        active={activeFilterIds.includes(option.id)}
                        onClick={() => onFilterToggle?.(option.id)}
                      >
                        {option.label}
                      </PickerButton>
                    ))}
                  </section>
                ))
              )}
              <PickerDivider />
              <PickerButton
                active={customFilterOpen}
                muted={!customFilterOpen}
                onClick={() => setCustomFilterOpen((value) => !value)}
              >
                <Glyph name="plus" className="size-3" />
                {t("resourceToolbar.addCustomFilter")}
              </PickerButton>
              {customFilterOpen ? (
                <CustomFilterEditor
                  editorRef={customEditorRef}
                  fields={customFilterFields}
                  field={selectedCustomField}
                  fieldId={selectedCustomField?.id ?? ""}
                  operator={effectiveCustomOperator}
                  value={customValue}
                  error={customValueError}
                  onField={(id) => {
                    const nextField = customFilterFields.find(
                      (field) => field.id === id,
                    );
                    setCustomFieldId(id);
                    setCustomOperator(defaultOperator(nextField));
                    setCustomValue("");
                    setCustomValueError(undefined);
                  }}
                  onOperator={(operator) => {
                    setCustomOperator(operator);
                    setCustomValueError(undefined);
                  }}
                  onValue={(value) => {
                    setCustomValue(value);
                    setCustomValueError(undefined);
                  }}
                  onAdd={addCustomFilter}
                />
              ) : null}
            </PickerColumn>
            {favoritesEnabled ? (
              <PickerColumn
                stacked={!roomyPicker}
                icon={<Glyph name="star" className="size-3.5" />}
                title={t("resourceToolbar.favorites")}
              >
                {onFavoriteSave ? <PickerButton
                  active={favoriteOpen}
                  muted={!favoriteOpen}
                  onClick={() => setFavoriteOpen((value) => !value)}
                >
                  <Glyph name="plus" className="size-3" />
                  {t("resourceToolbar.saveCurrentSearch")}
                </PickerButton> : null}
                {favoriteOpen ? (
                  <form
                    className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs"
                    onSubmit={(event) => {
                      event.preventDefault();
                      saveFavorite();
                    }}
                  >
                    <Input
                      size="sm"
                      value={favoriteLabel}
                      aria-label={t("resourceToolbar.favoriteName")}
                      onChange={(event) =>
                        setFavoriteLabel(event.currentTarget.value)
                      }
                    />
                    <Button
                      type="submit"
                      size="sm"
                      variant="secondary"
                      className="justify-center"
                    >
                      {t("resourceToolbar.save")}
                    </Button>
                  </form>
                ) : null}
                {favorites.length === 0 ? (
                  <PickerMuted>
                    {t("resourceToolbar.noSavedSearches")}
                  </PickerMuted>
                ) : (
                  favorites.map((favorite) => <div key={favorite.id} className="flex min-w-0 items-center gap-1">
                    {editingFavoriteId === favorite.id ? <form className="flex min-w-0 gap-1" onSubmit={(event) => {
                      event.preventDefault();
                      onFavoriteRename?.(favorite.id, favoriteLabel);
                      setEditingFavoriteId(null);
                    }}><Input size="sm" aria-label={t("resourceToolbar.favoriteName")}
                        value={favoriteLabel} onChange={(event) => setFavoriteLabel(event.target.value)} />
                      <Button type="submit" size="sm" variant="secondary">{t("resourceToolbar.save")}</Button></form>
                    : <PickerButton onClick={() => onFavoriteSelect?.(favorite)}>{favorite.label}</PickerButton>}
                    {favorite.id.startsWith("favorite:") && onFavoritePin ? <Button type="button" size="iconSm" variant="ghost"
                      aria-label={t(favorite.pinned ? "resourceToolbar.unpinFavorite" : "resourceToolbar.pinFavorite")} aria-pressed={Boolean(favorite.pinned)}
                      onClick={() => onFavoritePin(favorite.id, !favorite.pinned)}><Glyph name="pin" /></Button> : null}
                    {favorite.id.startsWith("favorite:") && onFavoriteRename ? <Button type="button" size="iconSm" variant="ghost"
                      aria-label={t("resourceToolbar.renameFavorite")} onClick={() => { setFavoriteLabel(favorite.label); setEditingFavoriteId(favorite.id); }}>
                      <Glyph name="pencil" /></Button> : null}
                  </div>)
                )}
              </PickerColumn>
            ) : null}
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
    <RemovableChip tone="brand" size="sm" className="max-w-52" removeLabel={removeLabel} onRemove={onRemove}>
      <span className="shrink-0">{label}:</span>
      <span className="truncate">{value}</span>
    </RemovableChip>
  );
}

function PickerColumn({
  stacked,
  icon,
  title,
  children,
}: {
  stacked: boolean;
  icon: ReactNode;
  title: ReactNode;
  children: ReactNode;
}): ReactElement {
  return (
    <section className={cn(
      "min-w-0 p-3",
      stacked
        ? "border-b border-border-subtle last:border-b-0"
        : "border-r border-border-subtle last:border-r-0",
    )}>
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
        "flex h-7 min-w-0 items-center gap-2 rounded-6 px-2 text-left text-13 outline-none transition-colors focus-visible:focus-ring",
        active
          ? "bg-brand-soft font-medium text-brand-soft-text"
          : muted
            ? "text-fg-muted hover:bg-inset hover:text-fg"
            : "text-fg hover:bg-inset",
      )}
      aria-pressed={active}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function CustomFilterEditor({
  editorRef,
  fields,
  field,
  fieldId,
  operator,
  value,
  error,
  onField,
  onOperator,
  onValue,
  onAdd,
}: {
  editorRef: React.Ref<HTMLDivElement>;
  fields: readonly ResourceToolbarFilterField[];
  field: ResourceToolbarFilterField | undefined;
  fieldId: string;
  operator: ResourceToolbarCustomFilterOperator;
  value: string;
  error?: string;
  onField: (id: string) => void;
  onOperator: (operator: ResourceToolbarCustomFilterOperator) => void;
  onValue: (value: string) => void;
  onAdd: () => void;
}): ReactElement {
  const t = useUiT();
  const operators = operatorsForField(field);
  const needsValue = customFilterNeedsValue(operator);
  return (
    <div ref={editorRef} className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs">
      {fields.length === 0 ? (
        <PickerMuted>{t("resourceToolbar.noFilterFields")}</PickerMuted>
      ) : (
        <>
          <Select
            size="sm"
            value={fieldId}
            aria-label={t("resourceToolbar.filterField")}
            options={fields.map((item) => ({
              value: item.id,
              label: item.label,
              group: item.group,
            }))}
            onValueChange={onField}
          />
          <div className="flex min-w-0 flex-col gap-2">
            <Select
              size="sm"
              value={operator}
              className="min-w-0 flex-1"
              aria-label={t("resourceToolbar.filterOperator")}
              options={operators.map((item) => ({
                value: item,
                label: filterOperatorLabel(item),
              }))}
              onValueChange={(next) =>
                onOperator(next as ResourceToolbarCustomFilterOperator)
              }
            />
            {needsValue ? (
              field?.renderValue && !structuredFilterOperand(operator) ? (
                field.renderValue({ value, onValueChange: onValue })
              ) : (field?.options || field?.type === "boolean") &&
                !structuredFilterOperand(operator) ? (
                <Select
                  size="sm"
                  value={value}
                  className="min-w-0 flex-1"
                  aria-label={t("resourceToolbar.filterValue")}
                  placeholder={t("resourceToolbar.value")}
                  options={
                    field.type === "boolean"
                      ? [
                          { value: "true", label: t("list.yes") },
                          { value: "false", label: t("list.no") },
                        ]
                      : field.options ?? []
                  }
                  onValueChange={onValue}
                />
              ) : (
                <Input
                  size="sm"
                  type={
                    structuredFilterOperand(operator)
                      ? "text"
                      : filterInputType(field)
                  }
                  value={value}
                  placeholder={t("resourceToolbar.value")}
                  aria-label={t("resourceToolbar.filterValue")}
                  aria-invalid={Boolean(error)}
                  className="min-w-0 flex-1"
                  onChange={(event) => onValue(event.currentTarget.value)}
                />
              )
            ) : null}
          </div>
          {error ? (
            <p role="alert" className="text-xs text-danger-text">
              {error}
            </p>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="justify-center"
            disabled={!field || (needsValue && value.trim() === "")}
            onClick={onAdd}
          >
            {t("resourceToolbar.add")}
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
  options: readonly ResourceToolbarGroupOption[];
  option: ResourceToolbarGroupOption | undefined;
  optionId: string;
  granularity: ResourceViewGroupGranularity;
  onOption: (id: string) => void;
  onGranularity: (granularity: ResourceViewGroupGranularity) => void;
  onAdd: () => void;
}): ReactElement {
  const t = useUiT();
  const granularities = option?.granularities ?? [];
  return (
    <div className="mt-2 grid gap-2 rounded-6 border border-border-subtle bg-sheet p-2 shadow-xs">
      {options.length === 0 ? (
        <PickerMuted>{t("resourceToolbar.noGroupFields")}</PickerMuted>
      ) : (
        <>
          <Select
            size="sm"
            value={optionId}
            aria-label={t("resourceToolbar.groupField")}
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
              aria-label={t("resourceToolbar.groupGranularity")}
              options={granularities.map((item) => ({
                value: item,
                label: titleCase(item),
              }))}
              onValueChange={(next) =>
                onGranularity(next as ResourceViewGroupGranularity)}
            />
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            className="justify-center"
            onClick={onAdd}
          >
            {t("resourceToolbar.add")}
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
  option: ResourceToolbarGroupOption;
  groups: readonly ResourceViewGroup[];
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
}): ReactElement {
  const t = useUiT();
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const active = groups.some((group) => group.field === option.group.field);
  const granularities = option.granularities ?? [];
  const primaryGranularities = granularities.filter((granularity) =>
    PRIMARY_GROUP_GRANULARITIES.has(granularity),
  );
  const advancedGranularities = granularities.filter((granularity) =>
    !PRIMARY_GROUP_GRANULARITIES.has(granularity),
  );
  const visibleGranularities = advancedOpen
    ? granularities
    : primaryGranularities;
  const selectedGranularities = new Set(
    groups
      .filter((group) => group.field === option.group.field && group.granularity)
      .map((group) => group.granularity!),
  );

  return (
    <div className={cn("rounded-6", active && "bg-brand-soft")}>
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
        <div className="flex flex-wrap gap-0.5 px-2 pb-1">
          {visibleGranularities.map((granularity) => (
            <button
              key={granularity}
              type="button"
              className={cn(
                "h-5 rounded-6 px-1.5 text-2xs font-medium outline-none focus-visible:focus-ring",
                selectedGranularities.has(granularity)
                  ? "bg-brand text-on-brand"
                  : "text-fg-muted hover:bg-sheet",
              )}
              onClick={() => {
                const nextGroup = { ...option.group, granularity };
                const selected = groups.some((group) =>
                  resourceViewGroupsEqual(group, nextGroup));
                onGroupStackChange?.(
                  selected
                    ? groups.filter((group) =>
                      !resourceViewGroupsEqual(group, nextGroup))
                    : [...groups, nextGroup],
                );
              }}
            >
              {titleCase(granularity)}
            </button>
          ))}
          {advancedGranularities.length > 0 ? (
            <button
              type="button"
              className="h-5 rounded-6 px-1.5 text-2xs font-medium text-fg-muted outline-none hover:bg-sheet focus-visible:focus-ring"
              aria-expanded={advancedOpen}
              onClick={() => setAdvancedOpen((open) => !open)}
            >
              {t(advancedOpen
                ? "resourceToolbar.basicGranularity"
                : "resourceToolbar.advancedGranularity")}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function PickerDivider(): ReactElement {
  return <div className="my-1 border-t border-border-subtle" />;
}

function PickerMuted({ children }: { children: ReactNode }): ReactElement {
  return <p className={cn(textRoleVariants({ role: "meta" }), "px-2 py-1")}>{children}</p>;
}

export function ResourceViewSwitcher<TView extends string = ResourceViewKind>({
  view,
  onViewChange,
  mode = "resource",
  kinds,
  ariaLabel,
  className,
  favorites = [],
  onFavoriteSelect,
}: ResourceViewSwitcherProps<TView>): ReactElement | null {
  const t = useUiT();
  const options = mode === "layout"
    ? [
        {
          value: "list" as TView,
          label: t("resourceToolbar.listView"),
          icon: "list",
        },
        {
          value: "grid" as TView,
          label: t("resourceToolbar.gridView"),
          icon: "layout-grid",
        },
      ]
    : (kinds ?? DEFAULT_SWITCHER_KINDS).map((kind) => ({
        value: kind as TView,
        label: t(RESOURCE_VIEW_KIND_SWITCHER[kind].labelKey),
        icon: RESOURCE_VIEW_KIND_SWITCHER[kind].icon,
      }));
  if (mode === "resource" && options.length <= 1) return null;
  return (
    <div
      className={cn("flex items-center gap-1", className)}
      role="group"
      aria-label={ariaLabel ?? t("resourceToolbar.viewSwitcher")}
    >
      {favorites.length > 0 ? (
        <Select
          size="sm"
          value={undefined}
          placeholder={t("resourceToolbar.favorites")}
          aria-label={t("resourceToolbar.favorites")}
          options={favorites.map((favorite) => ({ value: favorite.id, label: favorite.label }))}
          onValueChange={(value) => {
            const favorite = favorites.find((item) => item.id === value);
            if (favorite) onFavoriteSelect?.(favorite);
          }}
        />
      ) : null}
      {options.map((option) => (
        <Button
          key={option.value}
          type="button"
          variant="ghost"
          size="iconSm"
          aria-label={option.label}
          aria-pressed={view === option.value}
          active={view === option.value}
          onClick={() => onViewChange?.(option.value)}
        >
          <Glyph name={option.icon} className="glyph" />
        </Button>
      ))}
    </div>
  );
}

function resourceViewGroupLabel(
  group: ResourceViewGroup,
  options: readonly ResourceToolbarGroupOption[],
): string {
  const declared = options.find((option) => option.group.field === group.field)
    ?.label;
  const field =
    typeof declared === "string" ? declared : groupFieldLabel(group.field);
  return group.granularity
    ? `${field} · ${titleCase(group.granularity)}`
    : field;
}

function groupGranularity(
  option: ResourceToolbarGroupOption | undefined,
  selected: ResourceViewGroupGranularity,
): ResourceViewGroupGranularity {
  const supported = option?.granularities ?? [];
  if (supported.includes(selected)) return selected;
  const declared = option?.group.granularity;
  return declared && supported.includes(declared)
    ? declared
    : supported[0] ?? "day";
}

function operatorsForField(
  field: ResourceToolbarFilterField | undefined,
): readonly ResourceToolbarCustomFilterOperator[] {
  return field?.operators ?? ["exact"];
}

function defaultOperator(
  field: ResourceToolbarFilterField | undefined,
): ResourceToolbarCustomFilterOperator {
  const operators = operatorsForField(field);
  const preferred = field?.type === "text"
    ? (["iContains", "contains", "exact"] as const)
    : (["exact"] as const);
  return preferred.find((operator) => operators.includes(operator))
    ?? operators[0]
    ?? "exact";
}

function operatorForField(
  field: ResourceToolbarFilterField | undefined,
  operator: ResourceToolbarCustomFilterOperator,
): ResourceToolbarCustomFilterOperator {
  return operatorsForField(field).includes(operator)
    ? operator
    : defaultOperator(field);
}

function customFilterNeedsValue(
  operator: ResourceToolbarCustomFilterOperator,
): boolean {
  return operator !== "isNull" && operator !== "isNotNull";
}

function filterInputType(field: ResourceToolbarFilterField | undefined): string {
  if (field?.type === "number") return "number";
  if (field?.type === "date") return "date";
  if (field?.type === "datetime") return "datetime-local";
  return "text";
}

function coerceFilterValue(
  field: ResourceToolbarFilterField,
  value: string,
  operator: ResourceToolbarCustomFilterOperator,
): FilterValue | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  if (structuredFilterOperand(operator)) {
    return JSON.parse(trimmed) as FilterValue;
  }
  if (field.type === "number") {
    const number = Number(trimmed);
    return Number.isFinite(number) ? number : undefined;
  }
  if (field.type === "boolean") {
    return trimmed === "true" ? true : trimmed === "false" ? false : undefined;
  }
  return trimmed;
}

/** Multi-value and JSON operands are authored as JSON, then checked by ResourceQuery. */
function structuredFilterOperand(operator: ResourceToolbarCustomFilterOperator): boolean {
  return ["inList", "notInList", "hasKeysAny", "hasKeysAll", "jsonContains", "jsonContainedIn"].includes(operator);
}
