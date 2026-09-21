// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { ComparisonRows } from "./ComparisonRows";

afterEach(cleanup);

test("renders labeled before/after rows and associated evidence details", () => {
  render(<ComparisonRows
    fieldLabel="Field"
    beforeLabel="Before"
    afterLabel="After"
    rows={[{
      key: "supplier",
      label: "Supplier",
      before: "Old supplier",
      after: "New supplier",
      changed: true,
      details: <a href="/evidence/1">Source evidence</a>,
    }]}
  />);

  const table = screen.getByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Before" })).toBeTruthy();
  expect(within(table).getByText("Old supplier")).toBeTruthy();
  expect(within(table).getByText("New supplier")).toBeTruthy();
  expect(within(table).getByRole("link", { name: "Source evidence" })).toBeTruthy();
  expect(within(table).getByText("Supplier").closest("tr")?.dataset.changed).toBe("true");
});
