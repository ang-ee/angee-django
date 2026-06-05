import * as React from "react";

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
import { buildGroupOptions } from "./ListView";
import {
  FlatListBody,
  SelectionBar,
  type ListColumn,
} from "./ListInternals";
import {
  activeFilterIdsFor,
  buildFilterOptions,
  nextFacetFilter,
} from "./list-view-utils";

export interface RowsListViewProps<TRow extends StringIdRow = StringIdRow> {
  rows: readonly TRow[];
  columns: readonly ListColumn<TRow>[];
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
}

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
  dataView,
}: RowsListViewProps<TRow> & {
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
    () => groupOptions ?? buildGroupOptions(columns, defaultGroup),
    [columns, defaultGroup, groupOptions],
  );
  const groupingEnabled =
    toolbarGroupOptions.length > 0 || dataView.state.groupStack.length > 0;
  const filterOptions = React.useMemo(
    () => buildFilterOptions(columns, surface.sourceRows),
    [columns, surface.sourceRows],
  );
  const activeFilterIds = activeFilterIdsFor(
    dataView.state.filter,
    filterOptions,
  );
  const filterText = rowTextFilterValue(dataView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);

  return (
    <>
      <ControlBand>
        <DataToolbar
          className={controlBandItemClassName}
          pager={toolbarPager}
          group={groupingEnabled ? dataView.state.group : undefined}
          groupStack={groupingEnabled ? dataView.state.groupStack : undefined}
          groupOptions={groupingEnabled ? toolbarGroupOptions : undefined}
          filterOptions={filterOptions}
          activeFilterIds={activeFilterIds}
          filterText={filterText}
          onClearGroup={groupingEnabled ? () => dataView.setGroupStack([]) : undefined}
          onGroupStackChange={groupingEnabled ? dataView.setGroupStack : undefined}
          onPageChange={dataView.setPage}
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
          />
        ) : null}
        {error ? (
          <div className="px-3 py-6 text-13 text-danger-text">
            {error.message}
          </div>
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
