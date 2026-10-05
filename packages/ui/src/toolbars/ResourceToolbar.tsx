import type { ResourceSearch, SearchFacet } from "../views/resource/search/types";
import { SearchBox } from "../views/resource/search/SearchBox";
import type { ReactElement, ReactNode } from "react";
import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { titleCase } from "../lib/titleCase";
import { Button } from "../ui/button";
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
  resourceViewKindCapabilities,
} from "../views/resource/resource-view-model";
import { useResourceViewKindContent, useResourceViewKinds } from "../views/resource/resource-view-kinds";
import { labelText } from "../views/resource/resource-view-utils";

export interface ResourceToolbarChrome {
  viewSwitcher?: boolean;
  pager?: boolean;
}

export interface ResourceToolbarProps {
  search: ResourceSearch;
  pager: PagerState;
  chrome?: ResourceToolbarChrome;
  view?: ResourceViewKind;
  /** Present filter-option or shipped-preset ids and facets in a compact row. */
  filterRow?: { quickFilterIds?: readonly string[]; facetIds?: readonly string[] };
  createLabel?: ReactNode;
  onCreate?: () => void;
  actions?: ReactNode;
  utilityActions?: ReactNode;
  viewControls?: ResourceToolbarViewControls;
  availableViews?: readonly ResourceViewKind[];
  viewSwitcher?: ReactNode;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (pageSize: number) => void;
  pagerPageSizeOptions?: readonly number[];
  pagerMaxPageSize?: number;
  onViewChange?: (view: ResourceViewKind) => void;
  pagerSubject?: string;
  pagerTotalUnit?: string;
  className?: string;
  wrap?: boolean;
}

export interface ResourceToolbarFilterOption {
  id: string;
  label: ReactNode;
  chipLabel?: ReactNode;
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
  /** Granularities whose groups can be opened; number parts with no matching filter are left out. */
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
  favorites?: readonly ResourceViewFavorite[];
  onFavoriteSelect?: (favorite: ResourceViewFavorite) => void;
}

const DEFAULT_SWITCHER_KINDS: readonly ResourceViewKind[] = ["list", "board"];

export function ResourceToolbar({
  search, pager, chrome, view, filterRow, createLabel, onCreate, actions,
  utilityActions, viewControls, availableViews, viewSwitcher, onPageChange,
  onPageSizeChange, pagerPageSizeOptions, pagerMaxPageSize, onViewChange,
  pagerSubject, pagerTotalUnit, className, wrap = false,
}: ResourceToolbarProps): ReactElement {
  const t = useUiT();
  const { catalog, active } = search;
  // Keep the legacy filterRow adapter until the declaration cutover in step 5.
  const filterOptions: readonly ResourceToolbarFilterOption[] = [
    ...catalog.filters,
    ...catalog.facets.flatMap((facet) => facet.options.map((option) => ({ ...option, chipLabel: option.label }))),
  ];
  const favorites = catalog.favorites;
  const activeFilterIds = active.flatMap((item) => item.kind === "filter" ? [item.id.slice("filter:".length)]
    : item.kind === "facet" ? item.options.map((option) => option.id) : []);
  const activeFavoriteIds = active.flatMap((item) => item.kind === "favorite" ? [item.id.slice("favorite:".length)] : []);
  const onFilterToggle = (id: string) => {
    const facet = catalog.facets.find((candidate) => candidate.options.some((option) => option.id === id));
    if (facet) search.toggleFacetOption(facet.field, id);
    else search.toggleFilter(id);
  };
  const onFacetChange = (field: string, id: string | null) => search.setFacet(field, id ? [id] : []);
  const onFavoriteSelect = (favorite: ResourceViewFavorite) => search.applyFavorite(favorite.id);
  const onFavoriteToggle = (favorite: ResourceViewFavorite) => search.toggleFavorite(favorite.id);
  const onQueryReset = search.clearQuery;
  const resolvedCreateLabel = createLabel ?? t("resourceToolbar.create");
  // The active kind's applicability gates the data controls: the calendar shows
  // none of filter/pager/group-by; a surface that names no kind keeps them all.
  const capabilities = resourceViewKindCapabilities(view, useResourceViewKindContent(view)?.capabilities);
  const groupControls = capabilities.grouping && search.groupingEnabled;
  const clearable = search.queryDirty;
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
              facets={catalog.facets}
              filterOptions={filterOptions}
              activeFilterIds={activeFilterIds}
              activeFavoriteIds={activeFavoriteIds}
              queryDirty={clearable}
              onFilterToggle={onFilterToggle}
              onFacetChange={onFacetChange}
              onFavoriteToggle={onFavoriteToggle}
              onQueryReset={onQueryReset}
            />
          ) : null}
          <SearchBox search={groupControls ? search : { ...search, groupingEnabled: false }} box={filterRow ? "collapsed" : true} />
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
  favorites, quickFilterIds, facetIds, facets, filterOptions,
  activeFilterIds, activeFavoriteIds, queryDirty,
  onFilterToggle, onFacetChange, onFavoriteToggle, onQueryReset,
}: {
  favorites: readonly ResourceViewFavorite[];
  quickFilterIds: readonly string[];
  facetIds: readonly string[];
  facets: readonly SearchFacet[];
  filterOptions: readonly ResourceToolbarFilterOption[];
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
      type="button" size="sm" variant="ghost" className="rounded-full" active={activeFavoriteIds.includes(favorite.id)}
      aria-pressed={activeFavoriteIds.includes(favorite.id)} onClick={() => onFavoriteToggle?.(favorite)}>
      {favorite.label}</Button>)}
    {quickFilters.map((option) => <Button key={option.id} type="button" size="sm" variant="ghost" className="rounded-full"
      active={option.active} aria-pressed={option.active}
      onClick={option.onClick}>{option.label}</Button>)}
    {facetIds.map((field) => {
      const facet = facets.find((option) => option.field === field);
      const choices = facet?.options ?? [];
      const selected = choices.find((choice) => activeFilterIds.includes(choice.id));
      if (choices.length === 0) return null;
      const label = facet?.label ?? titleCase(field);
      return <Select key={field} size="sm" aria-label={labelText(label) ?? titleCase(field)}
        value={selected?.id ?? ""} placeholder={label}
        options={[{ value: "", label }, ...choices.map((choice) => ({ value: choice.id, label: choice.label }))]}
        onValueChange={(value) => onFacetChange?.(field, value || null)} />;
    })}
    {queryDirty && onQueryReset ? <Button type="button" size="sm" variant="ghost"
      onClick={onQueryReset}>{t("resourceToolbar.clear")}</Button> : null}
  </div>;
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
  const viewKinds = useResourceViewKinds();
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
    : (kinds ?? DEFAULT_SWITCHER_KINDS).flatMap((kind) => {
        // The collection's `#views` declare each kind's label and glyph.
        const content = viewKinds.get(kind);
        return content ? [{
          value: kind as TView,
          label: content.labelKey ? t(content.labelKey) : content.label ?? kind,
          icon: content.icon,
        }] : [];
      });
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
