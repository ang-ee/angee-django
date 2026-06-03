import * as React from "react";
import type { Row } from "@angee/sdk";

import {
  ControlBand,
  controlBandItemClassName,
} from "../shell/ControlBand";
import { DataToolbar } from "../toolbars";
import type { PagerState } from "../ui/pager";
import { Spinner } from "../ui/spinner";
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
  type DataViewContextValue,
} from "./data-view-context";
import { DeletePreviewDialog } from "./DeletePreviewDialog";
import { useDataViewSurface } from "./data-view-surface";
import {
  FlatListBody,
  SelectionBar,
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
import { useBulkDelete } from "./useBulkDelete";

export type { ListViewState } from "./data-view-surface";
export type {
  ColumnAlign,
  ListColumn,
} from "./list-internals";
export type { ListViewProps } from "./list-view-types";

const EMPTY_GROUP_STACK = [] as const;

export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
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
    <DataViewProvider initialState={initialState}>
      <ListViewBound {...props} />
    </DataViewProvider>
  );
}

function ListViewBound<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  return <ListViewBody {...props} dataView={useDataView()} />;
}

function ListViewBody<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  onCreate,
  createLabel,
  onRowClick,
  onListStateChange,
  rowHref,
  emptyMessage = "No records.",
  className,
  dataView,
}: ListViewProps<TRow> & {
  dataView: DataViewContextValue;
}): React.ReactElement {
  const surface = useDataViewSurface({
    model,
    columns,
    fields,
    filter,
    order,
    pageSize,
    dataView,
    groupStack: EMPTY_GROUP_STACK,
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
          filterOptions={filterOptions}
          visibleFields={surface.visibleFields}
          activeFilterIds={activeFilterIds}
          filterText={filterText}
          createLabel={createLabel ?? createLabelForModel(model)}
          onCreate={onCreate}
          onVisibleFieldToggle={surface.toggleVisibleField}
          onPageChange={setPage}
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
        {surface.list.error ? (
          <div className="px-3 py-6 text-13 text-danger-text">
            {surface.list.error.message}
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
            dataView={dataView}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            fetching={surface.list.fetching}
          />
        )}
        {surface.list.fetching ? (
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
