import * as React from "react";

import { Button } from "../ui/button";
import { Glyph } from "../chrome/Glyph";
import type { DndPayload } from "../lib/dnd";
import { GalleryView } from "./GalleryView";
import {
  type ResourceToolbarFilterField,
  type ResourceToolbarFilterOption,
  type ResourceToolbarGroupOption,
} from "../toolbars";
import type { PagerState } from "../ui/pager";
import {
  ResourceViewProvider,
  useResourceView,
  useResourceViewMaybe,
  type ResourceViewContextValue,
} from "./resource-view-context";
import {
  resourceViewGroupsEqual,
  type ResourceViewGroup,
} from "./resource-view-model";
import {
  nextRowTextFilter,
  rowTextFilterValue,
  useRowsResourceViewSurface,
  type ResourceListSnapshot,
  type StringIdRow,
} from "./resource-view-surface";
import {
  FlatListBody,
  type ListColumn,
} from "./ListInternals";
import { ResourceListFrame } from "./ResourceListFrame";
import type { ListEmptyState } from "./list-view-types";
import {
  activeFilterIdsFor,
  addCustomFilter as addCustomFilterToFilter,
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  customFilterChipsFor,
  mergeFilterFields,
  mergeFilterOptions,
  nextFacetFilter,
  removeCustomFilter,
} from "./list-view-utils";

export interface RowsListViewProps<TRow extends StringIdRow = StringIdRow> {
  rows: readonly TRow[];
  columns: readonly ListColumn<TRow>[];
  filters?: readonly ResourceToolbarFilterOption[];
  filterFields?: readonly ResourceToolbarFilterField[];
  groupOptions?: readonly ResourceToolbarGroupOption[];
  defaultGroup?: ResourceViewGroup | null;
  pageSize?: number;
  fetching?: boolean;
  error?: Error | null;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  rowHref?: (row: TRow) => string;
  emptyMessage?: React.ReactNode;
  emptyState?: ListEmptyState;
  className?: string;
  selectable?: boolean;
  /** Controls rendered in the toolbar's leading slot, beside the filter. */
  toolbarActions?: React.ReactNode;
  /**
   * Opt into a List/Grid switcher: when set, the toolbar gains a layout toggle
   * and the Grid mode renders each row as a {@link GalleryView} card over the
   * same filtered/paged surface. Navigation reuses `rowHref`/`onRowClick`.
   */
  gallery?: RowsGalleryConfig<TRow>;
  /** Bulk actions rendered in the selection bar when rows are selected. */
  bulkActions?: (
    selectedIds: ReadonlySet<string>,
    clear: () => void,
  ) => React.ReactNode;
  /** Make each row/card draggable by returning its dnd payload, or `null`. */
  draggableRow?: (row: TRow) => DndPayload | null;
  /** Use local resource-view state even when rendered inside another data view. */
  scope?: "inherit" | "local";
}

/** Card presentation for {@link RowsListViewProps.gallery}; mirrors GalleryView. */
export interface RowsGalleryConfig<TRow extends StringIdRow = StringIdRow> {
  image?: keyof TRow & string;
  title?: keyof TRow & string;
  subtitle?: keyof TRow & string;
  renderCard?: (row: TRow) => React.ReactNode;
}

type RowLayout = "list" | "grid";

export function RowsListView<TRow extends StringIdRow = StringIdRow>(
  props: RowsListViewProps<TRow>,
): React.ReactElement {
  const resourceView = useResourceViewMaybe();
  const scope = props.scope ?? "inherit";
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (scope !== "local" && resourceView) {
    return <RowsListViewBody {...props} resourceView={resourceView} />;
  }
  const providerScope = scope === "local" ? "local" : "route";
  return (
    <ResourceViewProvider initialState={initialState} scope={providerScope}>
      <RowsListViewBound {...props} />
    </ResourceViewProvider>
  );
}

function RowsListViewBound<TRow extends StringIdRow = StringIdRow>(
  props: RowsListViewProps<TRow>,
): React.ReactElement {
  return <RowsListViewBody {...props} resourceView={useResourceView()} />;
}

function RowsListViewBody<TRow extends StringIdRow = StringIdRow>({
  rows,
  columns,
  filters: explicitFilters,
  filterFields: explicitFilterFields,
  groupOptions,
  defaultGroup,
  pageSize,
  fetching = false,
  error = null,
  onRowClick,
  onListStateChange,
  rowHref,
  emptyMessage = "No records.",
  emptyState,
  className,
  selectable = false,
  toolbarActions,
  gallery,
  bulkActions,
  draggableRow,
  resourceView,
}: RowsListViewProps<TRow> & {
  resourceView: ResourceViewContextValue;
}): React.ReactElement {
  const emptyContent = emptyState ?? emptyMessage;
  const [layout, setLayout] = React.useState<RowLayout>("list");
  const handledDefaultGroupRef = React.useRef<ResourceViewGroup | null>(null);
  React.useEffect(() => {
    if (!defaultGroup) {
      handledDefaultGroupRef.current = null;
      return;
    }
    if (
      handledDefaultGroupRef.current
      && resourceViewGroupsEqual(handledDefaultGroupRef.current, defaultGroup)
    ) {
      return;
    }
    handledDefaultGroupRef.current = defaultGroup;
    if (resourceView.state.group === null) resourceView.setGroup(defaultGroup);
  }, [resourceView.setGroup, resourceView.state.group, defaultGroup]);

  const surface = useRowsResourceViewSurface({
    rows,
    columns,
    pageSize,
    resourceView,
    fetching,
    error,
    onListStateChange,
  });
  const toolbarPager = React.useMemo<PagerState>(
    () => ({
      total: surface.list.total,
      page: surface.list.page,
      pageSize: surface.list.pageSize,
      hasPrev: surface.list.hasPrev,
      hasNext: surface.list.hasNext,
    }),
    [
      surface.list.hasNext,
      surface.list.hasPrev,
      surface.list.page,
      surface.list.pageSize,
      surface.list.total,
    ],
  );
  const toolbarGroupOptions = React.useMemo(
    () => groupOptions ?? buildGroupOptions(columns, null, defaultGroup),
    [columns, defaultGroup, groupOptions],
  );
  const groupingEnabled =
    toolbarGroupOptions.length > 0 || resourceView.state.groupStack.length > 0;
  const inferredFilterFields = React.useMemo(
    () => buildFilterFields(columns, surface.sourceRows, null),
    [columns, surface.sourceRows],
  );
  const filterFields = React.useMemo(
    () => mergeFilterFields(explicitFilterFields, inferredFilterFields),
    [explicitFilterFields, inferredFilterFields],
  );
  const inferredFilterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.sourceRows, inferredFilterFields),
    [columns, inferredFilterFields, surface.sourceRows],
  );
  const filterOptions = React.useMemo(
    () => mergeFilterOptions(explicitFilters, inferredFilterOptions),
    [explicitFilters, inferredFilterOptions],
  );
  const activeFilterIds = activeFilterIdsFor(
    resourceView.state.filter,
    filterOptions,
  );
  const customFilterChips = customFilterChipsFor(
    resourceView.state.filter,
    filterOptions,
    filterFields,
  );
  const filterText = rowTextFilterValue(resourceView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);

  return (
    <ResourceListFrame
      className={className}
      toolbar={{
        actions: toolbarActions,
        viewSwitcher: gallery ? (
          <RowLayoutSwitcher layout={layout} onLayoutChange={setLayout} />
        ) : undefined,
        pager: toolbarPager,
        group: groupingEnabled ? resourceView.state.group : undefined,
        groupStack: groupingEnabled ? resourceView.state.groupStack : undefined,
        groupOptions: groupingEnabled ? toolbarGroupOptions : undefined,
        filterOptions,
        filterFields,
        customFilterChips,
        favorites: resourceView.savedFavorites,
        activeFilterIds,
        filterText,
        onClearGroup: groupingEnabled ? () => resourceView.setGroupStack([]) : undefined,
        onGroupStackChange: groupingEnabled ? resourceView.setGroupStack : undefined,
        onPageChange: resourceView.setPage,
        onPageSizeChange: resourceView.setPageSize,
        onCustomFilterAdd: (customFilter) =>
          resourceView.setFilter(
            addCustomFilterToFilter(resourceView.state.filter, customFilter),
          ),
        onCustomFilterRemove: (id) =>
          resourceView.setFilter(removeCustomFilter(resourceView.state.filter, id)),
        onFavoriteSave: resourceView.saveFavorite,
        onFavoriteSelect: resourceView.applyFavorite,
        onFilterToggle: (id) =>
          resourceView.setFilter(
            nextFacetFilter(resourceView.state.filter, filterOptions, id),
          ),
        onFilterTextChange: (value) =>
          resourceView.setFilter(nextRowTextFilter(resourceView.state.filter, value)),
      }}
      selection={
        selectable
          ? {
              count: surface.selectedIds.size,
              onClear: resourceView.clearSelectedIds,
              actions: surface.selectedIds.size > 0
                ? bulkActions?.(surface.selectedIds, resourceView.clearSelectedIds)
                : undefined,
            }
          : undefined
      }
      error={error}
      loadingFooter={fetching && surface.rowModels.length > 0}
    >
      {gallery && layout === "grid" ? (
        <GalleryView<TRow>
          rows={surface.rowModels.map((model) => model.original)}
          imageField={gallery.image}
          titleField={gallery.title}
          subtitleField={gallery.subtitle}
          renderCard={gallery.renderCard}
          cardHref={rowHref}
          onCardClick={onRowClick}
          draggableRow={draggableRow}
          selectedIds={selectable ? surface.selectedIds : undefined}
          onToggleSelected={selectable ? resourceView.toggleSelectedId : undefined}
          fetching={fetching}
          emptyMessage={emptyMessage}
          emptyState={emptyState}
        />
      ) : (
        <FlatListBody
          columns={columns}
          table={surface.table}
          rowModels={surface.rowModels}
          listItems={surface.listItems}
          expandedKeys={surface.expandedKeys}
          onToggleGroup={surface.toggleGroup}
          tableScrollRef={surface.tableScrollRef}
          rowVirtualizer={surface.rowVirtualizer}
          visibleColumnCount={surface.visibleColumnCount}
          allPageSelected={surface.allPageSelected}
          somePageSelected={surface.somePageSelected}
          onPageSelectionChange={surface.setPageSelection}
          visibleFields={surface.visibleFields}
          onVisibleFieldToggle={surface.toggleVisibleField}
          resourceView={resourceView}
          interactive={interactive}
          selectable={selectable}
          rowHref={rowHref}
          onRowClick={onRowClick}
          draggableRow={draggableRow}
          emptyMessage={emptyContent}
          fetching={fetching}
        />
      )}
    </ResourceListFrame>
  );
}

function RowLayoutSwitcher({
  layout,
  onLayoutChange,
}: {
  layout: RowLayout;
  onLayoutChange: (layout: RowLayout) => void;
}): React.ReactElement {
  return (
    <div className="flex items-center gap-1" role="group" aria-label="Layout">
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label="List view"
        aria-pressed={layout === "list"}
        active={layout === "list"}
        onClick={() => onLayoutChange("list")}
      >
        <Glyph name="list" className="glyph" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label="Grid view"
        aria-pressed={layout === "grid"}
        active={layout === "grid"}
        onClick={() => onLayoutChange("grid")}
      >
        <Glyph name="layout-grid" className="glyph" />
      </Button>
    </div>
  );
}
