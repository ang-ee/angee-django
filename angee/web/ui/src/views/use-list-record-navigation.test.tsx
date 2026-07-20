// @vitest-environment happy-dom

import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import type { ResourceListSnapshot } from "./resource-view-surface";
import type { ResourceViewFilter } from "./resource-view-model";
import { useListRecordNavigation } from "./use-list-record-navigation";

interface TestRow extends Record<string, unknown> {
  id: string;
}

test("replays the next list page and opens its first record", async () => {
  const onSelect = vi.fn();
  const { result } = renderHook(() =>
    useListRecordNavigation<TestRow>({ recordId: "row-a", onSelect }),
  );

  act(() => {
    result.current.onListStateChange(snapshot({
      rows: [{ id: "row-a" }],
      page: 1,
      hasNext: true,
    }));
  });

  act(() => result.current.navigation?.onNext?.());
  expect(result.current.navigationScope?.page).toBe(2);

  act(() => {
    result.current.onListStateChange(snapshot({
      rows: [],
      page: 2,
      hasNext: false,
      hasPrev: true,
      fetching: true,
    }));
  });
  expect(result.current.listState?.rows).toEqual([{ id: "row-a" }]);

  act(() => {
    result.current.onListStateChange(snapshot({
      rows: [{ id: "row-b" }],
      page: 2,
      hasNext: false,
      hasPrev: true,
    }));
  });

  await waitFor(() => expect(onSelect).toHaveBeenCalledWith("row-b"));
});

test("adopts a grouped scope snapshot instead of retaining a stale flat folder scope", () => {
  // No open record: the visible list drives the pager scope directly.
  const { result } = renderHook(() =>
    useListRecordNavigation<TestRow>({ recordId: null }),
  );

  // A flat single-folder list is visible: its snapshot captures the folder scope.
  act(() => {
    result.current.onListStateChange(
      scopedSnapshot([{ id: "row-a" }], { folder: { exact: "folder-a" } }),
    );
  });
  expect(result.current.navigationScope?.filter).toEqual({
    folder: { exact: "folder-a" },
  });

  // Returning to grouped "All files": the grouped surface now emits an empty-rows
  // snapshot carrying the All-files scope. The pager scope must follow it, not keep
  // the stale folder — otherwise a hidden replay pages the wrong folder's records.
  act(() => {
    result.current.onListStateChange(
      scopedSnapshot([], { is_trashed: { exact: false } }, { fetching: true }),
    );
  });
  expect(result.current.navigationScope?.filter).toEqual({
    is_trashed: { exact: false },
  });
});

function snapshot({
  rows,
  page,
  hasNext,
  hasPrev = false,
  fetching = false,
}: {
  rows: readonly TestRow[];
  page: number;
  hasNext: boolean;
  hasPrev?: boolean;
  fetching?: boolean;
}): ResourceListSnapshot<TestRow> {
  return {
    rows,
    total: 2,
    page,
    pageSize: 1,
    pageCount: 2,
    hasNext,
    hasPrev,
    fetching,
    navigationScope: {
      filter: { active: { exact: true } },
      order: { updated_at: "DESC" },
      page,
      pageSize: 1,
    },
  };
}

function scopedSnapshot(
  rows: readonly TestRow[],
  filter: ResourceViewFilter,
  { fetching = false }: { fetching?: boolean } = {},
): ResourceListSnapshot<TestRow> {
  return {
    rows,
    total: rows.length,
    page: 1,
    pageSize: 50,
    pageCount: 1,
    hasNext: false,
    hasPrev: false,
    fetching,
    navigationScope: {
      filter,
      order: { updated_at: "DESC" },
      page: 1,
      pageSize: 50,
    },
  };
}
