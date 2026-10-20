// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { ComparisonRows } from "./ComparisonRows";

afterEach(cleanup);

test("renders labeled before/after rows and associated evidence details", () => {
  render(<ComparisonRows
    fieldLabel="Field"
    columns={[{ key: "before", label: "Before" }, { key: "after", label: "After" }]}
    rows={[{
      key: "counterparty",
      label: "Counterparty",
      cells: { before: "Old counterparty", after: "New counterparty" },
      changed: true,
      details: <a href="/evidence/1">Source evidence</a>,
    }]}
  />);

  const table = screen.getByRole("table");
  expect(within(table).getByRole("columnheader", { name: "Before" })).toBeTruthy();
  expect(within(table).getByText("Old counterparty")).toBeTruthy();
  expect(within(table).getByText("New counterparty")).toBeTruthy();
  expect(within(table).getByRole("link", { name: "Source evidence" })).toBeTruthy();
  expect(within(table).getByText("Counterparty").closest("tr")?.dataset.changed).toBe("true");
});

test("a choice lets the reader pick one column per row", () => {
  const onChange = vi.fn();
  render(<ComparisonRows
    columns={[{ key: "left", label: "Ada" }, { key: "right", label: "Grace" }]}
    rows={[{ key: "notes", label: "Notes", cells: { left: "Met in 2024", right: "Met in 2025" } }]}
    choice={{ value: { notes: "left" }, onChange, label: (row) => `Keep ${String(row.label)}` }}
  />);

  const group = screen.getByRole("radiogroup", { name: "Keep Notes" });
  expect(within(group).getByRole("radio", { name: "Met in 2024" }).getAttribute("aria-checked")).toBe("true");
  fireEvent.click(within(group).getByRole("radio", { name: "Met in 2025" }));
  expect(onChange).toHaveBeenCalledWith("notes", "right");
});
