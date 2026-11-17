// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import { getCoreRowModel, getGroupedRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import type { Row } from "@angee/metadata";
import { expect, test } from "vitest";

import type { UiTranslate } from "../../i18n";
import { rowGroupsFromTableRows } from "./resource-view-codecs";

interface TaskRow extends Row { id: string; status: string | null }

const rows: TaskRow[] = [
  { id: "a", status: "open" },
  { id: "b", status: "done" },
  { id: "c", status: null },
];
const columns: ColumnDef<TaskRow>[] = [{ id: "status", accessorFn: (row) => row.status, getGroupingValue: (row) => row.status }];
const t: UiTranslate = (key) => key;

function tableRows(grouping: string[]) {
  return renderHook(() => useReactTable({
    data: rows, columns, getRowId: (row) => row.id, state: { grouping },
    getCoreRowModel: getCoreRowModel(), getGroupedRowModel: getGroupedRowModel(),
  })).result.current.getRowModel().rows;
}

test("a client-grouped lane's identity is its grouped value, null for the records with no value", () => {
  const groups = rowGroupsFromTableRows(tableRows(["status"]), [{ field: "status" }], t);
  expect(groups.map((group) => [group.key.startsWith("status:"), group.identity])).toEqual([
    [true, "open"], [true, "done"], [true, null],
  ]);
});

test("an ungrouped collection's single lane has no identity", () => {
  const [root] = rowGroupsFromTableRows(tableRows([]), [], t);
  expect(root?.key).toBe("root");
  expect(root).not.toHaveProperty("identity");
});
