// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { createTable, getCoreRowModel } from "@tanstack/react-table";
import { expect, test } from "vitest";
import { ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import type { UiTranslate } from "../../../i18n";
import { cellContent, formatMeasure, pressedSorting } from "./cell-utils";

const metadata = {
  fields: { status: { kind: "enum", values: [{ value: "open", description: "In progress" }] } },
} as unknown as ModelMetadata;
const t = ((key: string) => key) as UiTranslate;

test("enum metadata labels both plain cells and toned badges across GraphQL casing", () => {
  const plain = cellContent({ field: "status" }, { status: "OPEN" }, t, metadata);
  expect(plain).toBe("In progress");
  render(<>{cellContent({ field: "status", tone: { OPEN: "info" } }, { status: "OPEN" }, t, metadata)}</>);
  expect(screen.getByText("In progress")).toBeTruthy();
});

test("measures format numeric values and preserve other strings", () => {
  expect(formatMeasure(1234.5678, { unit: "ms" })).toBe("1,234.568 ms");
  expect(formatMeasure("12345678901234567890", { unit: "" })).toBe("12,345,678,901,234,567,890");
  expect(formatMeasure("unknown", { unit: "" })).toBe("unknown");
});

test("a Time scalar cell reads its clock label as written", () => {
  const time = { fields: { starts: { kind: "scalar", scalar: "Time" } } } as unknown as ModelMetadata;
  const { container } = render(<>{cellContent({ field: "starts" }, { starts: "13:45:00" }, t, time)}</>);
  expect(container.querySelector("time")?.getAttribute("datetime")).toBe("13:45:00");
  expect(screen.getByText("1:45 PM")).toBeTruthy();
  expect(cellContent({ field: "starts" }, { starts: null }, t, time)).toBe("—");
});

const tasks = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, status: { scalar: "String" }, kind: { scalar: "String" }, rank: { scalar: "Int" }, title: { scalar: "String" },
  due: { scalar: "Date" }, owner: { kind: "relation", identityPath: "owner.id", labelPath: "owner.name" }, "owner.name": { scalar: "String" },
} });
/** The order a press on `column` sets on a table grouped by `axes` ("due:month" a date by month) whose list holds `order`. */
function pressed(axes: readonly string[], order: string, column: string): string {
  const groupAxes = axes.map((id) => tasks.axis(id.split(":")[0]!, id.split(":")[1]));
  const sorting = order.split(", ").map((key) => ({ id: key.split(" ")[0]!, desc: key.endsWith(" desc") }));
  const table = createTable<Row>({
    data: [], columns: ["status", "kind", "rank", "title", "due", "owner.name"].map((id) => ({ id, accessorFn: () => null })),
    state: { sorting, columnPinning: {}, columnVisibility: {} }, onStateChange: () => {}, renderFallbackValue: null,
    getCoreRowModel: getCoreRowModel(), meta: { groupAxes, sorting },
  });
  return pressedSorting(table.getColumn(column)!, table).map(({ id, desc }) => `${id} ${desc ? "desc" : "asc"}`).join(", ");
}

test.each([
  // [what the order holds, the axes, its order, the grouping column pressed, the order the press sets]
  ["no key of the axis", ["status"], "rank desc", "status", "status desc, rank desc"],
  // Its key leads wherever the order held it, as the table is handed it, never left in place behind another key.
  ["the axis's key behind the records' keys", ["status"], "rank asc, status asc, title desc", "status", "status desc, rank asc, title desc"],
  ["another level's key and the records'", ["status", "kind"], "status asc, kind desc, rank asc", "kind", "kind asc, status asc, rank asc"],
  // A month level follows its date, which no grouping column of it replaces: another level's press keeps it.
  ["the date a month level follows", ["status", "due:month"], "due desc, rank asc", "status", "status desc, due desc, rank asc"],
  // The keys the pressed axis shares are the one it replaces: the relation's identity and its name, held twice.
  ["the keys a relation axis shares", ["owner"], "owner asc, title asc, owner.name desc", "owner.name", "owner.name desc, title asc"],
] as const)("a press on a grouping column, the order holding %s, turns its key and keeps every other key behind it in their order", (_name, axes, order, column, set) => {
  expect(pressed(axes, order, column)).toBe(set);
});
