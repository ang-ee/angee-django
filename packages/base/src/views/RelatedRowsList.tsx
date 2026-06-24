import * as React from "react";
import {
  useResourceList,
  type ResourceTypeName,
  type UseResourceListOptions,
} from "@angee/data";

import {
  RowsListView,
  type RowsListViewProps,
} from "./RowsListView";
import type { StringIdRow } from "./data-view-surface";

type RelatedRowsFilter = UseResourceListOptions<ResourceTypeName>["filter"];
type RelatedRowsOrder = UseResourceListOptions<ResourceTypeName>["order"];

export interface RelatedRowsListProps<
  TRow extends StringIdRow = StringIdRow,
> extends Omit<
    RowsListViewProps<TRow>,
    "rows" | "fetching" | "error" | "scope"
  > {
  model: string;
  recordId: string;
  fields: readonly string[];
  filterFor: (recordId: string) => RelatedRowsFilter;
  order?: RelatedRowsOrder;
  enabled?: boolean;
}

export function RelatedRowsList<
  TRow extends StringIdRow = StringIdRow,
>({
  model,
  recordId,
  fields,
  filterFor,
  order,
  enabled = true,
  pageSize,
  ...rowsListProps
}: RelatedRowsListProps<TRow>): React.ReactElement {
  const queryFields = React.useMemo(
    () => (fields.includes("id") ? fields : ["id", ...fields]),
    [fields],
  );
  const filter = React.useMemo(
    () => filterFor(recordId),
    [filterFor, recordId],
  );
  const { rows, fetching, error } = useResourceList(model, {
    fields: queryFields,
    filter,
    order,
    pageSize,
    enabled: enabled && Boolean(recordId),
  });

  return (
    <RowsListView<TRow>
      {...rowsListProps}
      rows={rows as readonly TRow[]}
      fetching={fetching}
      error={error}
      pageSize={pageSize}
      scope="local"
    />
  );
}
