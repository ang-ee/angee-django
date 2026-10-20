import type { ResourceSearch } from "../views/resource/search/types";
import type { ModelMetadata } from "@angee/metadata";
import { useContainerQuery } from "../lib/use-container-query";
import { SearchControls } from "../views/resource/search/SearchControls";
import { useSearchShortcuts, type ListSearchDeclaration } from "../views/resource/search/shortcuts";
import type { ReactElement, ReactNode } from "react";
import type { SortingState } from "@tanstack/react-table";
import { ScopeChip } from "../communication/pane-list";
import { PaneToolbar, PaneToolbarMenu, type PaneToolbarProps } from "../layouts/PaneToolbar";
import { SearchActiveLabel } from "../views/resource/search/SearchBox";
import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
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
  resourceViewGroupsEqual,
} from "../views/resource/resource-view-model";
import { useResourceViewKindContent, useResourceViewKinds } from "../views/resource/resource-view-kinds";

export interface ResourceToolbarChrome {
  viewSwitcher?: boolean;
  pager?: boolean;
  /** The search box, its shortcuts and group-by. */
  search?: boolean;
}

export interface ResourceToolbarProps {
  /** Pane chrome presents the same resource state in the host's shared band. */
  pane?: Pick<PaneToolbarProps, "selector" | "scope">;
  sortOptions?: readonly { value: string; label: string }[];
  sorting?: SortingState;
  onSortingChange?: (sorting: SortingState) => void;
  search: ResourceSearch;
  pager: PagerState;
  chrome?: ResourceToolbarChrome;
  view?: ResourceViewKind;
  /** List-owned presentation; the toolbar resolves its shortcuts through resource#search. */
  searchDeclaration?: ListSearchDeclaration;
  modelMetadata?: ModelMetadata | null;
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
  /** Extra resource fields the list selects while this option's filter is active,
   * such as the revision and permissions its bulk actions read. */
  fields?: readonly string[];
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

/**
 * @kind region
 * @region page-toolbar
 */
export function ResourceToolbar(props: ResourceToolbarProps): ReactElement {
  return props.pane ? <PaneResourceToolbar {...props} /> : <FullResourceToolbar {...props} />;
}

/** The pane presentation owns no query state; all commands belong to the resource. */
function PaneResourceToolbar({ search, pane, searchDeclaration, modelMetadata, sortOptions = [], sorting,
  onSortingChange, chrome, view, onViewChange, availableViews, viewSwitcher, actions, utilityActions,
  onCreate, createLabel, viewControls }: ResourceToolbarProps): ReactElement {
  const t = useUiT();
  const shortcuts = useSearchShortcuts(searchDeclaration, modelMetadata);
  const order = sorting?.[0];
  const firstGroup = search.groupStack[0];
  const groups = [
    ...(sortOptions.length && onSortingChange ? [{
      label: t("resourceToolbar.order"),
      value: order ? `${order.id}:${order.desc ? "desc" : "asc"}` : "",
      onValueChange: (value: string) => {
        const option = sortOptions.flatMap(({ value: field }) => [
          { value: `${field}:asc`, sorting: [{ id: field, desc: false }] },
          { value: `${field}:desc`, sorting: [{ id: field, desc: true }] },
        ]).find((option) => option.value === value);
        if (option) onSortingChange(option.sorting);
      },
      items: sortOptions.flatMap(({ value, label }) => [
        { value: `${value}:asc`, label: t("resourceToolbar.sortAscending", { label }) },
        { value: `${value}:desc`, label: t("resourceToolbar.sortDescending", { label }) },
      ]),
    }] : []),
    ...(search.groupingEnabled ? [{
      label: t("resourceToolbar.groupBy"),
      value: !firstGroup ? "" : search.groupStack.length === 1
        ? search.catalog.curatedGroups.find((item) => resourceViewGroupsEqual(item.group, firstGroup))?.id ?? "custom"
        : "custom",
      onValueChange: (value: string) => {
        const selected = search.catalog.curatedGroups.find((item) => item.id === value);
        if (!value || selected) search.setGroupStack(selected ? [selected.group] : []);
      },
      items: [{ value: "", label: t("resourceToolbar.noGrouping") },
        ...search.catalog.curatedGroups.map((item) => ({ value: item.id, label: item.label }))],
    }] : []),
  ];
  return <PaneToolbar selector={pane?.selector}
    scope={<>{pane?.scope}{search.active.map((item) => <ScopeChip key={item.id} active
      label={<SearchActiveLabel item={item} search={search} />} onClick={() => search.clear(item.id)} />)}</>}
    search={chrome?.search !== false ? <SearchControls search={{ ...search, groupingEnabled: false }}
      shortcuts={shortcuts} box="collapsed" /> : undefined}
    actions={<>{onCreate ? <ResourceCreateButton label={createLabel} onCreate={onCreate} /> : null}
      {actions}{viewControls ? <ResourceViewControls {...viewControls} /> : null}{utilityActions}
      {view && onViewChange && chrome?.viewSwitcher !== false ? <ResourceViewSwitcher view={view}
        kinds={availableViews} favorites={search.catalog.favorites} onFavoriteSelect={(favorite) => search.applyFavorite(favorite.id)}
        onViewChange={onViewChange} /> : null}{chrome?.viewSwitcher !== false ? viewSwitcher : null}</>}
    overflow={<PaneToolbarMenu groups={groups} />} />;
}

function FullResourceToolbar({
  search, pager, chrome, view, searchDeclaration, modelMetadata, createLabel, onCreate, actions,
  utilityActions, viewControls, availableViews, viewSwitcher, onPageChange,
  onPageSizeChange, pagerPageSizeOptions, pagerMaxPageSize, onViewChange,
  pagerSubject, pagerTotalUnit, className, wrap = false,
}: ResourceToolbarProps): ReactElement {
  const t = useUiT();
  const shortcuts = useSearchShortcuts(searchDeclaration, modelMetadata);
  const box = searchDeclaration?.box ?? (shortcuts.length ? "collapsed" : true);
  const [toolbarRef, roomy] = useContainerQuery<HTMLElement>(576);
  const favorites = search.catalog.favorites;
  const onFavoriteSelect = (favorite: ResourceViewFavorite) => search.applyFavorite(favorite.id);
  // The active kind's applicability gates the data controls: the calendar shows
  // none of filter/pager/group-by; a surface that names no kind keeps them all.
  const capabilities = resourceViewKindCapabilities(view, useResourceViewKindContent(view)?.capabilities);
  const groupControls = capabilities.grouping && search.groupingEnabled;
  return (
    <section
      ref={toolbarRef}
      aria-label={t("resourceToolbar.controls")}
      className={cn(
        "resource-toolbar min-h-11 border-b border-border-subtle bg-sheet px-3 py-2",
        wrap && "resource-toolbar-wrap",
        className,
      )}
    >
      <div className="resource-toolbar-actions">
        {onCreate ? <ResourceCreateButton label={createLabel} onCreate={onCreate} /> : null}
        {actions}
        {viewControls ? <ResourceViewControls {...viewControls} /> : null}
      </div>
      {capabilities.filter && chrome?.search !== false ? (
        <div
          className="resource-toolbar-query flex min-w-0 flex-wrap items-center gap-2"
        >
          <SearchControls search={groupControls ? search : { ...search, groupingEnabled: false }}
            shortcuts={shortcuts} box={box} narrow={!roomy} />
        </div>
      ) : null}
      <div className="resource-toolbar-utilities">
        {utilityActions}
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

/** The resource pager, placed by the toolbar or by the pane's list body. */
export function ResourceToolbarPager({ pager, pagerSubject, pagerTotalUnit, pagerPageSizeOptions,
  pagerMaxPageSize, onPageChange, onPageSizeChange }: ResourceToolbarProps): ReactElement {
  return <Pager {...pager} subject={pagerSubject} unit={pagerTotalUnit}
    pageSizeOptions={pagerPageSizeOptions} maxPageSize={pagerMaxPageSize}
    onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} />;
}

/** A collection's create command, in the toolbar or an embedded list's heading row. */
export function ResourceCreateButton({ label, onCreate }: {
  label?: ReactNode;
  onCreate: () => void;
}): ReactElement {
  const t = useUiT();
  return (
    <Button type="button" variant="primary" size="sm" onClick={onCreate}>
      <Glyph name="plus" className="glyph" />
      {label ?? t("resourceToolbar.create")}
    </Button>
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

/**
 * @owner ResourceToolbar
 */
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
