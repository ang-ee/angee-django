import * as React from "react";

import { Button } from "../ui/button";
import { Glyph } from "../chrome/Glyph";
import type { DndPayload } from "../lib/dnd";
import { GalleryView } from "./GalleryView";
import {
  ControlBand,
  controlBandItemClassName,
} from "../shell/ControlBand";
import {
  DataToolbar,
  type DataToolbarFilterField,
  type DataToolbarFilterOption,
  type DataToolbarGroupOption,
} from "../toolbars";
import type { PagerState } from "../ui/pager";
import { Spinner } from "../ui/spinner";
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
  type DataViewContextValue,
} from "./data-view-context";
import {
  dataViewGroupsEqual,
  type DataViewGroup,
} from "./data-view-model";
import {
  nextRowTextFilter,
  rowTextFilterValue,
  useRowsDataViewSurface,
  type ListViewState,
  type StringIdRow,
} from "./data-view-surface";
import {
  FlatListBody,
  SelectionBar,
  type ListColumn,
} from "./ListInternals";
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
  filters?: readonly DataToolbarFilterOption[];
  filterFields?: readonly DataToolbarFilterField[];
  groupOptions?: readonly DataToolbarGroupOption[];
  defaultGroup?: DataViewGroup | null;
  pageSize?: number;
  fetching?: boolean;
  error?: Error | null;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ListViewState<TRow>) => void;
  rowHref?: (row: TRow) => string;
  emptyMessage?: React.ReactNode;
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
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (dataView) return <RowsListViewBody {...props} dataView={dataView} />;
  return (
    <DataViewProvider initialState={initialState}>
      <RowsListViewBound {...props} />
    </DataViewProvider>
  );
}

function RowsListViewBound<TRow extends StringIdRow = StringIdRow>(
  props: RowsListViewProps<TRow>,
): React.ReactElement {
  return <RowsListViewBody {...props} dataView={useDataView()} />;
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
  className,
  selectable = false,
  toolbarActions,
  gallery,
  bulkActions,
  draggableRow,
  dataView,
}: RowsListViewProps<TRow> & {
  dataView: DataViewContextValue;
}): React.ReactElement {
  const [layout, setLayout] = React.useState<RowLayout>("list");
  const handledDefaultGroupRef = React.useRef<DataViewGroup | null>(null);
  React.useEffect(() => {
    if (!defaultGroup) {
      handledDefaultGroupRef.current = null;
      return;
    }
    if (
      handledDefaultGroupRef.current
      && dataViewGroupsEqual(handledDefaultGroupRef.current, defaultGroup)
    ) {
      return;
    }
    handledDefaultGroupRef.current = defaultGroup;
    if (dataView.state.group === null) dataView.setGroup(defaultGroup);
  }, [dataView.setGroup, dataView.state.group, defaultGroup]);

  const surface = useRowsDataViewSurface({
    rows,
    columns,
    pageSize,
    dataView,
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
    toolbarGroupOptions.length > 0 || dataView.state.groupStack.length > 0;
  const inferredFilterFields = React.useMemo(
    () => buildFilterFields(columns, surface.sourceRows, null),
    [columns, surface.sourceRows],
  );
  const inferredFilterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.sourceRows, inferredFilterFields),
    [columns, inferredFilterFields, surface.sourceRows],
  );
  const filterOptions = React.useMemo(
    () => mergeFilterOptions(explicitFilters, inferredFilterOptions),
    [explicitFilters, inferredFilterOptions],
  );
  const filterFields = React.useMemo(
    () => mergeFilterFields(explicitFilterFields, inferredFilterFields),
    [explicitFilterFields, inferredFilterFields],
  );
  const activeFilterIds = activeFilterIdsFor(
    dataView.state.filter,
    filterOptions,
  );
  const customFilterChips = customFilterChipsFor(
    dataView.state.filter,
    filterOptions,
    filterFields,
  );
  const filterText = rowTextFilterValue(dataView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);

  return (
    <>
      <ControlBand>
        <DataToolbar
          className={controlBandItemClassName}
          actions={toolbarActions}
          viewSwitcher={
            gallery ? (
              <RowLayoutSwitcher layout={layout} onLayoutChange={setLayout} />
            ) : undefined
          }
          pager={toolbarPager}
          group={groupingEnabled ? dataView.state.group : undefined}
          groupStack={groupingEnabled ? dataView.state.groupStack : undefined}
          groupOptions={groupingEnabled ? toolbarGroupOptions : undefined}
          filterOptions={filterOptions}
          filterFields={filterFields}
          customFilterChips={customFilterChips}
          favorites={dataView.savedFavorites}
          activeFilterIds={activeFilterIds}
          filterText={filterText}
          onClearGroup={groupingEnabled ? () => dataView.setGroupStack([]) : undefined}
          onGroupStackChange={groupingEnabled ? dataView.setGroupStack : undefined}
          onPageChange={dataView.setPage}
          onPageSizeChange={dataView.setPageSize}
          onCustomFilterAdd={(customFilter) =>
            dataView.setFilter(
              addCustomFilterToFilter(dataView.state.filter, customFilter),
            )
          }
          onCustomFilterRemove={(id) =>
            dataView.setFilter(removeCustomFilter(dataView.state.filter, id))
          }
          onFavoriteSave={dataView.saveFavorite}
          onFavoriteSelect={dataView.applyFavorite}
          onFilterToggle={(id) =>
            dataView.setFilter(
              nextFacetFilter(dataView.state.filter, filterOptions, id),
            )
          }
          onFilterTextChange={(value) =>
            dataView.setFilter(nextRowTextFilter(dataView.state.filter, value))
          }
        />
      </ControlBand>
      <div
        className={[
          "min-h-0 overflow-hidden bg-sheet",
          className,
        ]
          .filter(Boolean)
          .join(" ")}
      >
        {selectable && surface.selectedIds.size > 0 ? (
          <SelectionBar
            count={surface.selectedIds.size}
            onClear={dataView.clearSelectedIds}
            actions={bulkActions?.(
              surface.selectedIds,
              dataView.clearSelectedIds,
            )}
          />
        ) : null}
        {error ? (
          <div className="px-3 py-6 text-13 text-danger-text">
            {error.message}
          </div>
        ) : gallery && layout === "grid" ? (
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
            onToggleSelected={
              selectable ? dataView.toggleSelectedId : undefined
            }
          />
        ) : (
          <FlatListBody
            columns={columns}
            table={surface.table}
            rowModels={surface.rowModels}
            listItems={surface.listItems}
            tableScrollRef={surface.tableScrollRef}
            rowVirtualizer={surface.rowVirtualizer}
            visibleColumnCount={surface.visibleColumnCount}
            allPageSelected={surface.allPageSelected}
            somePageSelected={surface.somePageSelected}
            onPageSelectionChange={surface.setPageSelection}
            visibleFields={surface.visibleFields}
            onVisibleFieldToggle={surface.toggleVisibleField}
            dataView={dataView}
            interactive={interactive}
            selectable={selectable}
            rowHref={rowHref}
            onRowClick={onRowClick}
            draggableRow={draggableRow}
            emptyMessage={emptyMessage}
            fetching={fetching}
          />
        )}
        {fetching ? (
          <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
            <Spinner size="sm" />
            Loading...
          </div>
        ) : null}
      </div>
    </>
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
