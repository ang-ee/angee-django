import * as React from "react";
import {
  useAuthoredRows,
  type AuthoredQueryOptions,
  type AuthoredRowsOptions,
  type DocumentData,
  type TypedDocumentNode,
} from "@angee/sdk";

import {
  RowsListView,
  type RowsListViewProps,
} from "./RowsListView";
import type { StringIdRow } from "./data-view-surface";

export interface AuthoredRowsListProps<
  TDocument extends TypedDocumentNode<unknown, any>,
  TRow extends StringIdRow = StringIdRow,
> extends Omit<
    RowsListViewProps<TRow>,
    "rows" | "fetching" | "error"
  > {
  document: TDocument;
  variables?: AuthoredRowsOptions<TDocument, TRow>["variables"];
  queryOptions?: AuthoredQueryOptions;
  selectRows: (data: DocumentData<TDocument> | undefined) => readonly TRow[];
}

export function AuthoredRowsList<
  TDocument extends TypedDocumentNode<unknown, any>,
  TRow extends StringIdRow = StringIdRow,
>({
  document,
  variables,
  queryOptions,
  selectRows,
  ...rowsListProps
}: AuthoredRowsListProps<TDocument, TRow>): React.ReactElement {
  const { rows, fetching, error } = useAuthoredRows(document, {
    ...(queryOptions ?? {}),
    variables,
    selectRows,
  });

  return (
    <RowsListView<TRow>
      {...rowsListProps}
      rows={rows}
      fetching={fetching}
      error={error}
    />
  );
}
