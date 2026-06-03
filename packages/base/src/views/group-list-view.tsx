import * as React from "react";
import type { Row } from "@angee/sdk";

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
import { BoardView } from "./board-view";
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
  groupPagerStatesEqual,
  type GroupPagerState,
} from "./grouped-list";
import {
  FlatListBody,
  SelectionBar,
  dataViewGroupToAggregateDimension,
  groupFieldLabel,
  looksLikeDateField,
} from "./list-internals";
import type { ListViewProps } from "./list-view-types";
import {
  activeFilterIdsFor,
  buildFilterOptions,
  createLabelForModel,
  nextFacetFilter,
  nextTextFilter,
  textFilterValue,
} from "./list-view-utils";
import type { ColumnDescriptor } from "./page";
import { useBulkDelete } from "./useBulkDelete";

// GroupListView is a superset of the lean list: it owns the grouping-only
// `defaultGroup` (seeded here, its sole owner — DataPage just forwards it).
export type GroupListViewProps<TRow extends Row = Row> = ListViewProps<TRow> & {
  defaultGroup?: DataViewGroup | null;
};

export function GroupListView<TRow extends Row = Row>(
  props: GroupListViewProps<TRow>,
): React.ReactElement {
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (dataView) return <GroupListViewBody {...props} dataView={dataView} />;
  return (
    <DataViewProvider initialState={initialState}>
      <GroupListViewBound {...props} />
    </DataViewProvider>
  );
}

function GroupListViewBound<TRow extends Row = Row>(
  props: GroupListViewProps<TRow>,
): React.ReactElement {
  return <GroupListViewBody {...props} dataView={useDataView()} />;
}

function GroupListViewBody<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  defaultGroup,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  emptyMessage = "No records.",
  className,
  dataView,
}: GroupListViewProps<TRow> & {
  dataView: DataViewContextValue;
}): React.ReactElement {
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

  const groupDimensions = React.useMemo(
    () => dataView.state.groupStack.map(dataViewGroupToAggregateDimension),
    [dataView.state.groupStack],
  );
  const groupedListMode =
    dataView.state.view === "list" && groupDimensions.length > 0;
  const surface = useDataViewSurface({
    model,
    columns,
    fields,
    filter,
    order,
    pageSize,
    dataView,
    enabled: !groupedListMode,
    onListStateChange,
  });
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
  const groupOptions = React.useMemo(
    () => buildGroupOptions(columns, defaultGroup),
    [columns, defaultGroup],
  );
  const filterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.rows),
    [columns, surface.rows],
  );
  const activeFilterIds = activeFilterIdsFor(
    dataView.state.filter,
    filterOptions,
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
          view={dataView.state.view}
          group={dataView.state.group}
          groupStack={dataView.state.groupStack}
          groupOptions={groupOptions}
          filterOptions={filterOptions}
          visibleFields={surface.visibleFields}
          activeFilterIds={activeFilterIds}
          filterText={filterText}
          createLabel={createLabel ?? createLabelForModel(model)}
          onCreate={onCreate}
          onClearGroup={() => dataView.setGroupStack([])}
          onGroupStackChange={dataView.setGroupStack}
          onVisibleFieldToggle={surface.toggleVisibleField}
          onViewChange={dataView.setView}
          onPageChange={setPage}
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
        ) : dataView.state.view === "board" ? (
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

function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  defaultGroup: DataViewGroup | null | undefined,
): readonly DataToolbarGroupOption[] {
  const options: DataToolbarGroupOption[] = [];
  const seen = new Set<string>();
  const addOption = (option: DataToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  if (defaultGroup) {
    addOption({
      id: defaultGroup.field,
      label: groupFieldLabel(defaultGroup.field),
      group: defaultGroup,
      type: looksLikeDateField(defaultGroup.field) ? "date" : "value",
    });
  }

  for (const column of columns) {
    if (looksLikeDateField(column.field)) {
      addOption({
        id: column.field,
        label: groupFieldLabel(column.field),
        group: { field: column.field, granularity: "day" },
        type: "date",
      });
      continue;
    }
    if (column.field === "status" || column.tone) {
      addOption({
        id: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        group: { field: column.field },
        type: "value",
      });
    }
  }

  return options;
}
