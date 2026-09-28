import * as React from "react";
import type { ReactElement, ReactNode } from "react";
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
} from "../views/resource/resource-view-model";
import {
  resourceViewGroupsEqual,
  resourceViewKindCapabilities,
} from "../views/resource/resource-view-model";
import { groupFieldLabel } from "../views/resource/resource-view-list-body";
import { labelText } from "../views/resource/resource-view-utils";
import {
  FilterClauseEditor,
  type FilterClause,
  type FilterClauseField,
} from "./FilterClauseEditor";

export interface ResourceToolbarProps {
  pager: PagerState;
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
  customFilterFields?: readonly FilterClauseField[];
  customFilterChips?: readonly ResourceToolbarCustomFilterChip[];
  favorites?: readonly ResourceViewFavorite[];
  activeFilterIds?: readonly string[];
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
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pagerPageSizeOptions?: readonly number[];
  pagerMaxPageSize?: number;
  onViewChange?: (view: ResourceViewKind) => void;
  onCustomFilterAdd?: (filter: FilterClause) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
  /** Clear filter, sorting and grouping state together. */
  onQueryReset?: () => void;
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
}

/** Per-kind switcher chrome — the label key + glyph, keyed by kind. */
const RESOURCE_VIEW_KIND_SWITCHER: Record<
  ResourceViewKind,
  { labelKey: string; icon: string }
> = {
  list: { labelKey: "resourceToolbar.listView", icon: "list" },
  board: { labelKey: "resourceToolbar.boardView", icon: "grid-2x2" },
  calendar: { labelKey: "resourceToolbar.calendarView", icon: "calendar" },
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
  activeFilterIds = [],
  filterText = "",
  createLabel,
  onCreate,
  actions,
  utilityActions,
  viewControls,
  availableViews,
  viewSwitcher,
  onFilterToggle,
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
  onQueryReset,
  queryDirty = false,
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
  return (
    <section
      aria-label={t("resourceToolbar.controls")}
      className={cn(
        "resource-toolbar min-h-11 border-b border-border-subtle bg-sheet px-3 py-2",
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
        <div className="resource-toolbar-query">
          <FilterPicker
            groups={groups}
            groupControls={groupControls}
            groupOptions={toolbarGroupOptions}
            customGroupOptions={toolbarCustomGroupOptions}
            activeFilters={activeFilters}
            activeFilterIds={activeFilterIds}
            filterOptions={filterOptions}
            customFilterFields={customFilterFields}
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
        </div>
      ) : null}
      <div className="resource-toolbar-utilities">
        {utilityActions}
        {queryDirty && onQueryReset ? (
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
        {capabilities.pagination ? (
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
        {view && onViewChange ? (
          <ResourceViewSwitcher
            view={view}
            kinds={availableViews}
            onViewChange={onViewChange}
          />
        ) : null}
        {viewSwitcher}
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

function FilterPicker({
  groups,
  groupControls,
  groupOptions,
  customGroupOptions,
  filterOptions,
  customFilterFields,
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
  groups: readonly ResourceViewGroup[];
  groupControls: boolean;
  groupOptions: readonly ResourceToolbarGroupOption[];
  customGroupOptions: readonly ResourceToolbarGroupOption[];
  filterOptions: readonly ResourceToolbarFilterOption[];
  customFilterFields: readonly FilterClauseField[];
  customFilterChips: readonly ResourceToolbarCustomFilterChip[];
  favorites: readonly ResourceViewFavorite[];
  activeFilters: readonly ResourceToolbarFilterOption[];
  activeFilterIds: readonly string[];
  filterText: string;
  onFilterTextChange?: (value: string) => void;
  onFilterToggle?: (id: string) => void;
  onClearGroup?: () => void;
  onGroupStackChange?: (groups: readonly ResourceViewGroup[]) => void;
  onCustomFilterAdd?: (filter: FilterClause) => void;
  onCustomFilterRemove?: (id: string) => void;
  onFavoriteSave?: (label: string) => void;
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
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
  const [customGroupOpen, setCustomGroupOpen] = React.useState(false);
  const [customGroupId, setCustomGroupId] = React.useState("");
  const [customGroupGranularity, setCustomGroupGranularity] =
    React.useState<ResourceViewGroupGranularity>("day");
  const selectedCustomGroup =
    customGroupOptions.find((option) => option.id === customGroupId) ??
    customGroupOptions[0];
  const effectiveCustomGroupGranularity = groupGranularity(
    selectedCustomGroup,
    customGroupGranularity,
  );
  const groupLabelOptions = React.useMemo(
    () => [...groupOptions, ...customGroupOptions],
    [customGroupOptions, groupOptions],
  );
  const [favoriteOpen, setFavoriteOpen] = React.useState(false);
  const [favoriteLabel, setFavoriteLabel] =
    React.useState(defaultFavoriteLabel);
  const favoritesEnabled = onFavoriteSave !== undefined;
  const {
    draft: draftFilterText,
    setDraft: setDraftFilterText,
    commit: commitFilterText,
  } = useDebouncedText(filterText, onFilterTextChange);

  function addCustomGroup() {
    if (!selectedCustomGroup || !onGroupStackChange) return;
    const group =
      selectedCustomGroup.type === "date"
        ? { ...selectedCustomGroup.group, granularity: effectiveCustomGroupGranularity }
        : selectedCustomGroup.group;
    if (groups.some((item) => resourceViewGroupsEqual(item, group))) {
      setCustomGroupOpen(false);
      return;
    }
    onGroupStackChange([...groups, group]);
    setCustomGroupOpen(false);
  }

  function saveFavorite() {
    const label = favoriteLabel.trim();
    if (!label || !onFavoriteSave) return;
    onFavoriteSave(label);
    setFavoriteLabel(defaultFavoriteLabel);
    setFavoriteOpen(false);
  }

  return (
    <PopoverRoot open={pickerOpen} onOpenChange={setPickerOpen}>
      <div
        ref={pickerHostRef}
        className={cn(
          "flex h-8 w-full min-w-0 items-center gap-1 rounded-6 border border-transparent bg-inset pl-2 pr-1 text-13 text-fg focus-within:border-border-focus focus-within:bg-sheet focus-within:focus-ring",
        )}
      >
        <Glyph name="search" className="size-3.5 shrink-0 text-fg-muted" />
        {groups.slice(0, roomyPicker ? undefined : 1).map((nextGroup, index) => (
          <FacetChip
            key={`${nextGroup.field}:${nextGroup.granularity ?? ""}`}
            label={
              index === 0
                ? t("resourceToolbar.groupBy")
                : t("resourceToolbar.then")
            }
            value={resourceViewGroupLabel(nextGroup, groupLabelOptions)}
            removeLabel={resourceViewGroupLabel(nextGroup, groupLabelOptions)}
            onRemove={() => {
              const next = groups.filter(
                (_, groupIndex) => groupIndex !== index,
              );
              if (next.length === 0) onClearGroup?.();
              else onGroupStackChange?.(next);
            }}
          />
        ))}
        {activeFilters.slice(0, roomyPicker ? undefined : Math.max(0, 1 - groups.length)).map((option) => (
          <FacetChip
            key={option.id}
            label={t("resourceToolbar.filter")}
            value={option.chipLabel ?? option.label}
            removeLabel={String(option.chipLabel ?? option.label)}
            onRemove={() => onFilterToggle?.(option.id)}
          />
        ))}
        {customFilterChips.slice(0, roomyPicker ? undefined : Math.max(0, 1 - groups.length - activeFilters.length)).map((chip) => (
          <FacetChip
            key={chip.id}
            label={t("resourceToolbar.filter")}
            value={chip.label}
            removeLabel={
              labelText(chip.label) ?? t("resourceToolbar.filterFallback")
            }
            onRemove={() => onCustomFilterRemove?.(chip.id)}
          />
        ))}
        {!roomyPicker && groups.length + activeFilters.length + customFilterChips.length > 1 ? (
          <button
            type="button"
            className="h-6 shrink-0 rounded-6 bg-brand-soft px-2 text-xs font-medium text-brand-soft-text outline-none focus-visible:focus-ring"
            onClick={() => setPickerOpen(true)}
          >
            +{groups.length + activeFilters.length + customFilterChips.length - 1}
          </button>
        ) : null}
        {onFilterTextChange && (
          <input
            type="search"
            value={draftFilterText}
            placeholder={t("resourceToolbar.filterPlaceholder")}
            aria-label={t("resourceToolbar.filterRecords")}
            className="h-full min-w-[7rem] flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted"
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
        )}
        <PopoverTrigger
          className="grid size-6 shrink-0 place-content-center rounded-6 text-fg-muted outline-none transition-colors hover:bg-sheet hover:text-fg focus-visible:focus-ring"
          aria-label={
            groupControls
              ? t(
                  favoritesEnabled
                    ? "resourceToolbar.filterGroupFavorites"
                    : "resourceToolbar.filterAndGroup",
                )
              : t(
                  favoritesEnabled
                    ? "resourceToolbar.filterAndFavorites"
                    : "resourceToolbar.filter",
                )
          }
        >
          <Glyph name="chevron-down" className="size-3" />
        </PopoverTrigger>
      </div>
      <PopoverPortal>
        <PopoverPositioner sideOffset={6} align="start">
          <PopoverContent
            className={cn(
              "grid max-h-[min(36rem,calc(100dvh-5rem))] max-w-[calc(100vw-1rem)] overflow-y-auto overscroll-contain",
              roomyPicker
                ? groupControls && favoritesEnabled
                  ? "w-[45rem] grid-cols-3"
                  : groupControls || favoritesEnabled
                    ? "w-[30rem] grid-cols-2"
                    : "w-[18rem] grid-cols-1"
                : "w-[min(22rem,calc(100vw-1rem))] grid-cols-1",
            )}
          >
            <PickerColumn
              stacked={!roomyPicker}
              icon={<Glyph name="filter" className="size-3.5" />}
              title={t("resourceToolbar.filters")}
            >
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
                <FilterClauseEditor
                  className="mt-2"
                  fields={customFilterFields}
                  onSubmit={(clause) => onCustomFilterAdd?.(clause)}
                />
              ) : null}
            </PickerColumn>
            {groupControls ? (
              <PickerColumn
                stacked={!roomyPicker}
                icon={<Glyph name="sliders-horizontal" className="size-3.5" />}
                title={t("resourceToolbar.groupBy")}
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
                  {t("resourceToolbar.addCustomGroup")}
                </PickerButton>
                {customGroupOpen ? (
                  <CustomGroupEditor
                    options={customGroupOptions}
                    option={selectedCustomGroup}
                    optionId={selectedCustomGroup?.id ?? ""}
                    granularity={effectiveCustomGroupGranularity}
                    onOption={(id) => {
                      const option = customGroupOptions.find(
                        (item) => item.id === id,
                      );
                      setCustomGroupId(id);
                      setCustomGroupGranularity(
                        groupGranularity(option, "day"),
                      );
                    }}
                    onGranularity={setCustomGroupGranularity}
                    onAdd={addCustomGroup}
                  />
                ) : null}
              </PickerColumn>
            ) : null}
            {favoritesEnabled ? (
              <PickerColumn
                stacked={!roomyPicker}
                icon={<Glyph name="star" className="size-3.5" />}
                title={t("resourceToolbar.favorites")}
              >
                <PickerButton
                  active={favoriteOpen}
                  muted={!favoriteOpen}
                  onClick={() => setFavoriteOpen((value) => !value)}
                >
                  <Glyph name="plus" className="size-3" />
                  {t("resourceToolbar.saveCurrentSearch")}
                </PickerButton>
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
}: ResourceViewSwitcherProps<TView>): ReactElement {
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
  return (
    <div
      className={cn("flex items-center gap-1", className)}
      role="group"
      aria-label={ariaLabel ?? t("resourceToolbar.viewSwitcher")}
    >
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
