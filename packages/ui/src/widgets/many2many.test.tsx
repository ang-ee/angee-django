// @vitest-environment happy-dom

import { fireEvent, render, screen, cleanup, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { Many2ManyCellEdit, many2manyWidget } from "./many2many";

afterEach(cleanup);

function choose(option: HTMLElement) {
  fireEvent.pointerDown(option, { pointerType: "mouse", button: 0 });
  fireEvent.click(option);
}

describe("many2manyWidget", () => {

  test("renders mixed relation records and scalar ids once by their option labels", () => {
    const Read = many2manyWidget.read;

    render(
      <Read
        value={[{ id: "skill-1" }, "skill-1", { id: "skill-2" }, { id: 7 }, 7, null, ""]}
        field={{
          options: [
            { value: "skill-1", label: "Planning" },
            { value: "skill-2", label: "Review" },
            { value: "7", label: "Numeric skill" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Planning")).toBeTruthy();
    expect(screen.getByText("Review")).toBeTruthy();
    expect(screen.getByText("Numeric skill")).toBeTruthy();
  });
});


test("edits multiple relations in one control, including removing stored ids outside the option list", async () => {
  const Edit = Many2ManyCellEdit;
  const onChange = vi.fn();
  const field = { label: "Categories", options: [
    { value: "primary", label: "Primary" },
    { value: "secondary", label: "Secondary" },
    { value: "off", label: "Disabled", disabled: true },
  ] };
  const { rerender } = render(<Edit value={["primary"]} field={field} onChange={onChange} />);
  const trigger = screen.getByRole("combobox", { name: "Categories" });
  expect(within(trigger).getByText("Primary")).toBeTruthy();
  fireEvent.click(trigger);
  choose(await screen.findByRole("option", { name: "Secondary" }));
  expect(onChange).toHaveBeenLastCalledWith(["primary", "secondary"]);
  rerender(<Edit value={["primary", "secondary"]} field={field} onChange={onChange} />);
  expect(within(trigger).getByText("+1")).toBeTruthy();
  choose(screen.getByRole("option", { name: "Primary" }));
  expect(onChange).toHaveBeenLastCalledWith(["secondary"]);
  rerender(<Edit value={["legacy"]} field={field} onChange={onChange} />);
  choose(await screen.findByRole("option", { name: "legacy" }));
  expect(onChange).toHaveBeenLastCalledWith([]);
  cleanup();
});

test("a read-only multiple relation has no picker", () => {
  const Edit = many2manyWidget.edit;
  render(<Edit value={["primary"]} field={{ options: [{ value: "primary", label: "Primary" }] }} readOnly />);
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.getByText("Primary")).toBeTruthy();
  cleanup();
});


test("full forms retain individually removable chips, including unloaded selections", () => {
  const Edit = many2manyWidget.edit;
  const onChange = vi.fn();
  render(<Edit value={[{ id: "known" }, "known", { id: "unloaded" }, null, ""]} field={{ options: [{ value: "known", label: "Known" }] }} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /unloaded/ }));
  expect(onChange).toHaveBeenLastCalledWith(["known"]);
  expect(screen.queryByText("+1")).toBeNull();
});
