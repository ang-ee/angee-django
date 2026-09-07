// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceToolbar, type ResourceToolbarFilterField } from "./ResourceToolbar";

afterEach(cleanup);

async function editor(field: ResourceToolbarFilterField) {
  const add = vi.fn();
  render(<ResourceToolbar pager={{ total: 0, page: 1, pageSize: 20 }} customFilterFields={[field]} onCustomFilterAdd={add} onFilterTextChange={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "Filter" }));
  fireEvent.click(await screen.findByRole("button", { name: "Add custom filter" }));
  return add;
}

test("invalid JSON stays in the editor with feedback, and an empty membership operand is submitted", async () => {
  const add = await editor({ id: "status", label: "Status", type: "selection", operators: ["inList"] });
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "[" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(screen.getByRole("alert").textContent).toBe("Enter valid JSON.");
  expect(screen.getByLabelText("Filter value").getAttribute("aria-invalid")).toBe("true");
  expect(add).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "[]" } });
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(add).toHaveBeenCalledWith({ field: "status", operator: "inList", value: [], type: "selection" });
});

test("Boolean operands use explicit Yes and No choices and retain false", async () => {
  const add = await editor({ id: "active", label: "Active", type: "boolean", operators: ["exact"] });
  const input = screen.getByLabelText("Filter value");
  expect(input.getAttribute("role")).toBe("combobox");
  fireEvent.click(input);
  const option = await screen.findByRole("option", { name: "No" });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(add).toHaveBeenCalledWith({ field: "active", operator: "exact", value: false, type: "boolean" });
});
