// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import type { ModelMetadata } from "@angee/metadata";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";

import {
  buildColumns,
  cellContent,
  groupMeasuresFromColumns,
  RowActionsHeader,
} from "./resource-view-list-body";

vi.mock("../i18n", () => ({
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
    { sort: null, setSort: vi.fn() },
    {},
  );
  const renderHeader = column?.header as (() => ReactNode) | undefined;

  render(<>{renderHeader?.()}</>);

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

test("routes boolean cell copy through the UI translator", () => {
  const t = (key: string) => ({ "list.yes": "Sí", "list.no": "No" })[key] ?? key;

  expect(cellContent({ field: "enabled" }, { id: "1", enabled: true }, t)).toBe("Sí");
  expect(cellContent({ field: "enabled" }, { id: "2", enabled: false }, t)).toBe("No");
});

test("renders a metadata-declared date scalar without relying on its name", () => {
  const metadata: ModelMetadata = {
    typeName: "ReleaseType",
    fields: {
      published: { name: "published", kind: "scalar", scalar: "DateTime" },
    },
  };

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
  const metadata: ModelMetadata = {
    typeName: "ReleaseType",
    fields: {
      published_at: { name: "published_at", kind: "scalar", scalar: "String" },
    },
  };

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
