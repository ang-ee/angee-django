import { RowsListView, type RowsListViewProps, type StringIdRow } from "@angee/ui";
import type { ReactElement } from "react";

import {
  useOperatorSnapshot,
  type OperatorSnapshotResult,
} from "../../data/transport";
import type {
  OperatorSnapshot,
  OperatorSnapshotSections,
} from "../../data/types";

export type OperatorRowsSelector<TRow extends StringIdRow> = (
  snapshot: OperatorSnapshot,
) => readonly TRow[];

export interface OperatorRowsResult<TRow extends StringIdRow>
  extends OperatorSnapshotResult {
  rows: readonly TRow[];
  fetching: boolean;
  error: Error | null;
}

export function useOperatorRows<TRow extends StringIdRow>(
  sections: OperatorSnapshotSections,
  selectRows: OperatorRowsSelector<TRow>,
): OperatorRowsResult<TRow> {
  const snapshotResult = useOperatorSnapshot(sections);
  const { snapshot, result } = snapshotResult;
  return {
    ...snapshotResult,
    rows: snapshot ? selectRows(snapshot) : [],
    fetching: result.fetching,
    error: snapshot ? null : result.error ?? null,
  };
}

export interface OperatorRowsListProps<TRow extends StringIdRow>
  extends Omit<RowsListViewProps<TRow>, "rows" | "fetching" | "error"> {
  sections: OperatorSnapshotSections;
  selectRows: OperatorRowsSelector<TRow>;
}

export function OperatorRowsList<TRow extends StringIdRow>({
  sections,
  selectRows,
  ...rowsListProps
}: OperatorRowsListProps<TRow>): ReactElement {
  const { rows, fetching, error } = useOperatorRows(sections, selectRows);
  return (
    <RowsListView<TRow>
      {...rowsListProps}
      rows={rows}
      fetching={fetching}
      error={error}
    />
  );
}
