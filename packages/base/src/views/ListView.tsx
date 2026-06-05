import * as React from "react";
import {
  useResourceAggregate,
  type ResourceTypeName,
  type Row,
  type UseAggregateOptions,
} from "@angee/sdk";

import {
  ControlBand,
  controlBandItemClassName,
} from "../shell/ControlBand";
import {
  DataToolbar,
  type DataToolbarGroupOption,
} from "../toolbars";
import type { PagerState } from "../ui/pager";
import { Spinner } from "../ui/spinner";
import { BoardView } from "./BoardView";
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
import { DeletePreviewDialog } from "./DeletePreviewDialog";
import { useDataViewSurface } from "./data-view-surface";
import {
  GroupedListBody,
} from "./GroupedList";
import {
  groupPagerStatesEqual,
  type GroupPagerState,
} from "./grouped-list-utils";
import {
  FlatListBody,
  SelectionBar,
  dataViewGroupToAggregateDimension,
  groupMeasuresFromColumns,
  type FlatListBodyProps,
} from "./ListInternals";
import type { ListViewProps } from "./list-view-types";
import {
  activeFilterIdsFor,
  addCustomFilter as addCustomFilterToFilter,
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  createLabelForModel,
  customFilterChipsFor,
  mergeFilterFields,
  mergeFilterOptions,
  nextFacetFilter,
  nextTextFilter,
  removeCustomFilter,
  textFilterValue,
} from "./list-view-utils";
import type { ColumnDescriptor } from "./page";
import { useBulkDelete } from "./useBulkDelete";

export type { ListViewState } from "./data-view-surface";
export type {
  ColumnAlign,
  ListColumn,
} from "./ListInternals";
export type { ListViewProps } from "./list-view-types";

// GroupListView is a superset of the lean list: it owns the grouping-only
// `defaultGroup` (seeded here, its sole owner — DataPage just forwards it).
export type GroupListViewProps<TRow extends Row = Row> = ListViewProps<TRow> & {
  defaultGroup?: DataViewGroup | null;
};

const EMPTY_GROUP_STACK = [] as const;

export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  return <ListViewShell {...props} grouping={false} />;
}

export function GroupListView<TRow extends Row = Row>(
  props: GroupListViewProps<TRow>,
): React.ReactElement {
  return <ListViewShell {...props} grouping />;
}

interface ListViewShellProps<TRow extends Row>
  extends GroupListViewProps<TRow> {
  grouping: boolean;
}

function ListViewShell<TRow extends Row = Row>(
  props: ListViewShellProps<TRow>,
): React.ReactElement {
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (dataView) return <ListViewBody {...props} dataView={dataView} />;
  return (
    <DataViewProvider initialState={initialState} resource={props.model}>
      <ListViewBound {...props} />
    </DataViewProvider>
  );
}

function ListViewBound<TRow extends Row = Row>(
  props: ListViewShellProps<TRow>,
): React.ReactElement {
  return <ListViewBody {...props} dataView={useDataView()} />;
}

function ListViewBody<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  filters: explicitFilters,
  filterFields: explicitFilterFields,
  groupOptions: explicitGroupOptions,
  order,
  pageSize,
  defaultGroup,
  grouping,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  emptyMessage = "No records.",
  className,
  dataView,
}: ListViewShellProps<TRow> & {
  dataView: DataViewContextValue;
}): React.ReactElement {
  const handledDefaultGroupRef = React.useRef<DataViewGroup | null>(null);
  React.useEffect(() => {
    if (!grouping || !defaultGroup) {
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
  }, [
    dataView.setGroup,
    dataView.state.group,
    defaultGroup,
    grouping,
  ]);

  const groupDimensions = React.useMemo(
    () =>
      grouping
        ? dataView.state.groupStack.map(dataViewGroupToAggregateDimension)
        : [],
    [dataView.state.groupStack, grouping],
  );
  const groupedListMode =
    grouping && dataView.state.view === "list" && groupDimensions.length > 0;
  const surface = useDataViewSurface({
    model,
    columns,
    fields,
    filter,
    order,
    pageSize,
    dataView,
    groupStack: grouping ? undefined : EMPTY_GROUP_STACK,
    enabled: !groupedListMode,
    onListStateChange,
  });
  const flatMeasures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const [groupPagerState, setGroupPagerState] =
    React.useState<GroupPagerState | null>(null);
  const handleGroupPagerStateChange = React.useCallback(
    (next: GroupPagerState) => {
      setGroupPagerState((current) =>
        groupPagerStatesEqual(current, next) ? current : next,
      );
    },
    [],
  );
  const toolbarPager = React.useMemo<PagerState>(() => {
    if (!groupedListMode) {
      return {
        total: surface.list.total,
        page: surface.list.page,
        pageSize: surface.list.pageSize,
        hasPrev: surface.list.hasPrev,
        hasNext: surface.list.hasNext,
      };
    }
    // Group-level pager: Pager derives hasPrev/hasNext from page/total.
    return {
      total: groupPagerState?.total ?? 0,
      page: dataView.state.page,
      pageSize: dataView.state.pageSize,
    };
  }, [
    dataView.state.page,
    dataView.state.pageSize,
    groupPagerState?.total,
    groupedListMode,
    surface.list.hasNext,
    surface.list.hasPrev,
    surface.list.page,
    surface.list.pageSize,
    surface.list.total,
  ]);
  const toolbarGroupOptions = React.useMemo(
    () =>
      grouping
        ? mergeGroupOptions(
            explicitGroupOptions,
            buildGroupOptions(columns, defaultGroup),
          )
        : undefined,
    [columns, defaultGroup, explicitGroupOptions, grouping],
  );
  const inferredFilterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.rows),
    [columns, surface.rows],
  );
  const filterOptions = React.useMemo(
    () => mergeFilterOptions(explicitFilters, inferredFilterOptions),
    [explicitFilters, inferredFilterOptions],
  );
  const inferredFilterFields = React.useMemo(
    () => buildFilterFields(columns, surface.rows),
    [columns, surface.rows],
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

  const setPage = React.useCallback(
    (page: number) => {
      dataView.setPage(page);
    },
    [dataView.setPage],
  );

  const filterText = textFilterValue(dataView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);
  const bulkDelete = useBulkDelete(
    model,
    surface.selectedIds,
    dataView.clearSelectedIds,
  );

  return (
    <>
      <ControlBand>
        <DataToolbar
          className={controlBandItemClassName}
          pager={toolbarPager}
          view={grouping ? dataView.state.view : undefined}
          group={grouping ? dataView.state.group : undefined}
          groupStack={grouping ? dataView.state.groupStack : undefined}
          groupOptions={toolbarGroupOptions}
          filterOptions={filterOptions}
          filterFields={filterFields}
          customFilterChips={customFilterChips}
          favorites={dataView.savedFavorites}
          activeFilterIds={activeFilterIds}
          filterText={filterText}
          createLabel={createLabel ?? createLabelForModel(model)}
          onCreate={onCreate}
          onClearGroup={grouping ? () => dataView.setGroupStack([]) : undefined}
          onGroupStackChange={grouping ? dataView.setGroupStack : undefined}
          onViewChange={grouping ? dataView.setView : undefined}
          onPageChange={setPage}
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
          pagerSubject={groupedListMode ? "Groups" : undefined}
          pagerTotalUnit={groupedListMode ? "groups" : undefined}
          onFilterToggle={(id) =>
            dataView.setFilter(
              nextFacetFilter(dataView.state.filter, filterOptions, id),
            )
          }
          onFilterTextChange={(value) =>
            dataView.setFilter(nextTextFilter(dataView.state.filter, value))
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
        {surface.selectedIds.size > 0 ? (
          <SelectionBar
            count={surface.selectedIds.size}
            onClear={dataView.clearSelectedIds}
            onDelete={bulkDelete.deleteInitiate}
            deletePending={bulkDelete.isPending}
          />
        ) : null}
        {groupedListMode ? (
          <GroupedListBody
            model={model}
            columns={columns}
            table={surface.table}
            tableColumns={surface.tableColumns}
            columnVisibility={surface.columnVisibility}
            visibleColumnCount={surface.visibleColumnCount}
            visibleFields={surface.visibleFields}
            onVisibleFieldToggle={surface.toggleVisibleField}
            dataView={dataView}
            groupDimensions={groupDimensions}
            requestedFields={surface.requestedFields}
            mergedFilter={surface.mergedFilter}
            sortOrder={surface.sortOrder}
            order={order}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            onPagerStateChange={handleGroupPagerStateChange}
          />
        ) : surface.list.error ? (
          <div className="px-3 py-6 text-13 text-danger-text">
            {surface.list.error.message}
          </div>
        ) : grouping && dataView.state.view === "board" ? (
          <BoardView
            columns={columns}
            groups={surface.groupedRows}
            dataView={dataView}
            selectedIds={surface.selectedIds}
            interactive={interactive}
            emptyMessage={emptyMessage}
            rowHref={rowHref}
            onRowClick={onRowClick}
          />
        ) : flatMeasures.length > 0 ? (
          <FlatListBodyWithAggregate
            model={model}
            filter={surface.mergedFilter}
            measures={flatMeasures}
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
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            fetching={surface.list.fetching}
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
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            fetching={surface.list.fetching}
          />
        )}
        {!groupedListMode && surface.list.fetching ? (
          <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
            <Spinner size="sm" />
            Loading...
          </div>
        ) : null}
        {bulkDelete.isPreviewOpen && bulkDelete.previewState ? (
          <DeletePreviewDialog
            preview={bulkDelete.previewState}
            recordCount={bulkDelete.previewRecordCount}
            blockedRecordCount={bulkDelete.previewBlockedRecordCount}
            overflowCount={bulkDelete.previewOverflowCount}
            isPending={bulkDelete.isPending}
            onConfirm={bulkDelete.onConfirm}
            onCancel={bulkDelete.onCancel}
          />
        ) : null}
      </div>
    </>
  );
}

function FlatListBodyWithAggregate<TRow extends Row>({
  model,
  filter,
  measures,
  ...props
}: FlatListBodyProps<TRow> & {
  model: string;
  filter: UseAggregateOptions<ResourceTypeName>["filter"];
  measures: UseAggregateOptions<ResourceTypeName>["measures"];
}): React.ReactElement {
  const aggregate = useResourceAggregate(model, {
    filter,
    measures,
    enabled: Boolean(measures?.length),
  });
  return <FlatListBody {...props} footerAggregate={aggregate.aggregate} />;
}

function mergeGroupOptions(
  explicit: readonly DataToolbarGroupOption[] | undefined,
  inferred: readonly DataToolbarGroupOption[],
): readonly DataToolbarGroupOption[] {
  const merged: DataToolbarGroupOption[] = [];
  const seen = new Set<string>();
  for (const option of [...(explicit ?? []), ...inferred]) {
    if (seen.has(option.id)) continue;
    seen.add(option.id);
    merged.push(option);
  }
  return merged;
}
