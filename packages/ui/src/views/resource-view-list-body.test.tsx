// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { expect, test, vi } from "vitest";

import {
  buildColumns,
  groupMeasuresFromColumns,
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
    { emptyValueLabel: "Empty" },
  );
  const renderHeader = column?.header as (() => ReactNode) | undefined;

  render(<>{renderHeader?.()}</>);

  expect(screen.getByText("Actions").classList.contains("sr-only")).toBe(true);
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
