import * as React from "react";
import { refineResourceName, type Row } from "@angee/metadata";
import { useList, type HttpError, type MetaQuery } from "@refinedev/core";
import { refineFieldsFromPaths } from "@angee/refine";
import { errorFromUnknown } from "../../../data/errors";
import { DEFAULT_TEXT_FILTER_FIELD } from "../resource-view-model";
import { useBoardLaneState } from "../resource-view-board-lanes";
import { leafTableRows, modelRowId, stringRowId } from "../resource-view-codecs";
import { useResourceViewPresentationSurface } from "./presentation";
import { CLIENT_ROW_MODEL_FETCH_CAP } from "./resource-surface";
import { listResultFromPageState, useResourceRowsSnapshot, useResourceViewQueryFacts } from "./table-state";
import type { ResourceListResult, ResourceViewSurface, RowRecord, RowsResourceViewSurface, StringIdRow, UseResourceViewSurfaceProps, UseRowsResourceViewSurfaceProps } from "./types";
/**
 * Surface a **client row-model** resource: fetch the whole set once (up to
 * ``CLIENT_ROW_MODEL_FETCH_CAP``) and filter/sort/paginate it in the browser
 * with the same Angee dialect engine the rows surface uses. The sibling of
 * :func:`useResourceViewSurface` (which keeps every list op on the server) — a
 * caller picks one by ``isClientRowModel(resource)`` at a component boundary, so
 * only the active path issues a query and resolves a data provider.
 */
export function useClientResourceViewSurface<TRow extends Row = Row>({
  columns,
  fields,
  filter,
  resourceView,
  modelMetadata = null,
  groupStack,
  laneSource,
  enabled = true,
  onListStateChange,
}: UseResourceViewSurfaceProps<TRow>): ResourceViewSurface<TRow> {
  const { requestedFields, mergedFilter, sortOrder } = useResourceViewQueryFacts({
    columns,
    fields,
    filter,
    resourceView,
    modelMetadata,
    laneSource,
    includeDeclaredOrder: false,
  });
  const dataResource = modelMetadata?.resource ?? null;
  const resourceName = dataResource ? refineResourceName(dataResource) : "__angee_disabled__";
  const listMeta = React.useMemo<MetaQuery>(
    () => ({ fields: refineFieldsFromPaths(requestedFields) }),
    [requestedFields],
  );
  const active = enabled && Boolean(dataResource);

  const run = useList<RowRecord, HttpError>({
    resource: resourceName,
    dataProviderName: dataResource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: 1,
      pageSize: CLIENT_ROW_MODEL_FETCH_CAP,
    },
    meta: listMeta,
    queryOptions: { enabled: active },
  });
  const allRows = React.useMemo(
    () => (run.result.data ?? []) as readonly RowRecord[] as readonly TRow[],
    [run.result.data],
  );
  const refetchRows = React.useCallback(() => {
    void run.query.refetch();
  }, [run.query.refetch]);
  const boardLaneState = useBoardLaneState<TRow>({
    laneSource,
    modelMetadata,
    rows: allRows,
    enabled: active && resourceView.state.view === "board",
    refetchRows,
  });
  // The fetched page is capped; the only signal the in-browser set is actually
  // incomplete is the resource's own total exceeding the cap (a page that
  // returned exactly the cap is not necessarily truncated).
  const totalRows = run.result.total;
  React.useEffect(() => {
    if (totalRows !== undefined && totalRows > CLIENT_ROW_MODEL_FETCH_CAP) {
      console.warn(
        `Client resource "${dataResource?.modelLabel ?? resourceName}" has ` +
          `${totalRows} rows, above the ${CLIENT_ROW_MODEL_FETCH_CAP}-row client ` +
          "fetch cap; in-browser filter/sort/group is incomplete. " +
          'Mark the resource rowModel="server" or narrow it.',
      );
    }
  }, [totalRows, dataResource?.modelLabel, resourceName]);

  const fetching = run.query.isFetching || boardLaneState.fetching;
  const error = errorFromUnknown(run.query.error);
  const refetch = React.useCallback(() => {
    void run.query.refetch();
  }, [run.query]);
  const presentation = useResourceViewPresentationSurface<TRow>({
    rows: allRows,
    columns,
    resourceView,
    modelMetadata,
    groupStack,
    boardLaneState,
    getRowId: modelRowId,
    filter: mergedFilter,
  });
  const pageRows = React.useMemo(
    () => leafTableRows(presentation.rowModels).map((row) => row.original),
    [presentation.rowModels],
  );
  const filteredTotal = presentation.table.getFilteredRowModel().rows.length;
  const pageCount = Math.max(1, presentation.table.getPageCount());
  const list = React.useMemo<ResourceListResult>(
    () =>
      listResultFromPageState({
        resourceView,
        error,
        fetching,
        refetch,
        rows: pageRows,
        total: filteredTotal,
        pageCount,
      }),
    [
      error,
      fetching,
      filteredTotal,
      pageCount,
      pageRows,
      refetch,
      resourceView,
    ],
  );
  const listState = useResourceRowsSnapshot<TRow>(list, { onListStateChange });

  return {
    kind: "flat",
    list,
    listState,
    rows: pageRows,
    requestedFields,
    mergedFilter,
    sortOrder,
    ...presentation,
  };
}

export function useRowsResourceViewSurface<
  TRow extends StringIdRow = StringIdRow,
>({
  rows,
  columns,
  resourceView,
  modelMetadata = null,
  groupStack,
  fetching = false,
  error = null,
  onListStateChange,
}: UseRowsResourceViewSurfaceProps<TRow>): RowsResourceViewSurface<TRow> {
  const textSearchFields = React.useMemo(
    () => columns.map((column) => column.field),
    [columns],
  );
  const presentation = useResourceViewPresentationSurface({
    rows,
    columns,
    resourceView,
    filter: resourceView.state.filter,
    textSearchField: DEFAULT_TEXT_FILTER_FIELD,
    textSearchFields,
    modelMetadata,
    groupStack,
    getRowId: stringRowId,
  });
  const pageRows = React.useMemo(
    () => leafTableRows(presentation.rowModels).map((row) => row.original),
    [presentation.rowModels],
  );
  const total = presentation.table.getFilteredRowModel().rows.length;
  const pageCount = Math.max(1, presentation.table.getPageCount());

  const listState = useResourceRowsSnapshot<TRow>({
    rows: pageRows,
    total,
    page: resourceView.state.page,
    pageSize: resourceView.state.pageSize,
    pageCount,
    hasNext: resourceView.state.page < pageCount,
    hasPrev: resourceView.state.page > 1,
    fetching,
    error,
  }, { onListStateChange });

  return {
    kind: "flat",
    list: listState,
    listState,
    rows: pageRows,
    sourceRows: rows,
    ...presentation,
  };
}
