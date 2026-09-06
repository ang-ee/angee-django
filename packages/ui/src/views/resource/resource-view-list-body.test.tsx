// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import { schemaFieldMetadataFromDataResources, type ModelMetadata } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { getCoreRowModel, useReactTable, flexRender } from "@tanstack/react-table";
import { expect, test, vi } from "vitest";

import {
  buildColumns,
  cellContent,
  groupMeasuresFromColumns,
  hasuraMeasuresFromGroupMeasures,
  RowActionsHeader,
} from "./resource-view-list-body";

vi.mock("../../i18n", () => ({
  useUiT: () => (key: string) => key,
}));

test("renders a visually hidden list-column header", () => {
  const [column] = buildColumns(
    [
      {
        field: "actions",
        header: "Actions",
        headerVisuallyHidden: true,
        sortable: false,
      },
    ],
    {},
  );
  function Header() {
    const table = useReactTable({ data: [], columns: [column!], getCoreRowModel: getCoreRowModel() });
    const header = table.getHeaderGroups()[0]!.headers[0]!;
    return <>{flexRender(header.column.columnDef.header, header.getContext())}</>;
  }
  render(<Header />);

  expect(screen.getByText("Actions").classList.contains("sr-only")).toBe(true);
});

test("renders the framework row-actions header as visually hidden copy", () => {
  render(
    <table>
      <thead>
        <tr>
          <RowActionsHeader />
        </tr>
      </thead>
    </table>,
  );

  expect(screen.getByText("list.actions").classList.contains("sr-only")).toBe(true);
});

test("projects count columns into aggregate measures", () => {
  expect(
    groupMeasuresFromColumns([
      { field: "id", header: "Files", aggregate: "count" },
      { field: "size_bytes", header: "Size", aggregate: "sum" },
    ]),
  ).toEqual([
    { op: "count", field: "id", columnId: "id", label: "Files", unit: "" },
    {
      op: "sum",
      field: "size_bytes",
      columnId: "size_bytes",
      label: "Size",
      unit: "",
    },
  ]);
});

test("resolves a column measure once to its server aggregate input", () => {
  const metadata = {
    resource: {
      aggregateMeasures: [{ op: "sum", field: "word_count", input: "WORD_COUNT" }],
    },
  } as unknown as ModelMetadata;
  const measures = groupMeasuresFromColumns([
    { field: "word_count", header: "Words", aggregate: "sum" },
  ]);

  expect(hasuraMeasuresFromGroupMeasures(measures, metadata)).toEqual([{
    op: "sum",
    field: "WORD_COUNT",
    input: "WORD_COUNT",
    columnId: "word_count",
    label: "Words",
    unit: "",
  }]);
});

test("routes boolean cell copy through the UI translator", () => {
  const t = (key: string) => ({ "list.yes": "Sí", "list.no": "No" })[key] ?? key;

  expect(cellContent({ field: "enabled" }, { id: "1", enabled: true }, t)).toBe("Sí");
  expect(cellContent({ field: "enabled" }, { id: "2", enabled: false }, t)).toBe("No");
});

test("renders a metadata-declared date scalar without relying on its name", () => {
  const metadata = modelMetadata("published", "DateTime");

  const { container } = render(
    <>{cellContent(
      { field: "published" },
      { id: "1", published: "2026-08-22T10:00:00Z" },
      (key) => key,
      metadata,
    )}</>,
  );

  expect(container.querySelector("time")?.getAttribute("datetime")).toBe(
    "2026-08-22T10:00:00.000Z",
  );
});

test("does not probe a date-looking field declared as a string", () => {
  const metadata = modelMetadata("published_at", "String");

  const { container } = render(
    <>{cellContent(
      { field: "published_at" },
      { id: "1", published_at: "2026-08-22T10:00:00Z" },
      (key) => key,
      metadata,
    )}</>,
  );

  expect(container.querySelector("time")).toBeNull();
  expect(screen.getByText("2026-08-22T10:00:00Z")).toBeTruthy();
});

function modelMetadata(name: string, scalar: string): ModelMetadata {
  const resource = testDataResource("tests.Row", {
    fields: [{
      name, kind: "scalar", scalar, readable: true, filterable: false,
      sortable: false, aggregatable: false, groupable: false, creatable: false,
      updatable: false, requiredOnCreate: false,
    }],
  });
  return schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
}
