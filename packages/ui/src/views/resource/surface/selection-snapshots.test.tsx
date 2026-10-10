// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import type { RowSelectionState } from "@tanstack/react-table";
import { expect, test } from "vitest";

import { useSelectionSnapshots } from "./table-state";

test("selected revisions survive page unloads until deselection, then reselection captures the new row", () => {
  const original = { id: "one", revision: 1 };
  const updated = { id: "one", revision: 2 };
  const hook = renderHook(({ rows, selection }: { rows: typeof original[]; selection: RowSelectionState }) =>
    useSelectionSnapshots(rows.map((original) => ({ id: original.id, original })), selection),
  { initialProps: { rows: [original], selection: { one: true } as RowSelectionState } });
  expect(hook.result.current.selectedRows).toEqual([original]);
  const ids = hook.result.current.selectedIds;
  hook.rerender({ rows: [], selection: { one: true } });
  expect(hook.result.current.selectedIds).toBe(ids);
  expect(hook.result.current.selectedRows).toEqual([original]);
  hook.rerender({ rows: [updated], selection: { one: true } });
  expect(hook.result.current.selectedRows).toEqual([original]);
  hook.rerender({ rows: [updated], selection: {} });
  expect(hook.result.current.selectedRows).toEqual([]);
  hook.rerender({ rows: [updated], selection: { one: true } });
  expect(hook.result.current.selectedRows).toEqual([updated]);
});
