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

  test("renders nested relation records by their option labels", () => {
    const Read = many2manyWidget.read;

    render(
      <Read
        value={[{ id: "skill-1" }, { id: "skill-2" }]}
        field={{
          options: [
            { value: "skill-1", label: "Planning" },
            { value: "skill-2", label: "Review" },
          ],
        }}
      />,
    );

    expect(screen.getByText("Planning")).toBeTruthy();
    expect(screen.getByText("Review")).toBeTruthy();
  });
});


test("edits multiple relations in one control, including removing stored ids outside the option list", async () => {
  const Edit = Many2ManyCellEdit;
  const onChange = vi.fn();
  const field = { label: "Taxes", options: [
    { value: "vat", label: "VAT 20%" },
    { value: "fee", label: "Service 5%" },
    { value: "off", label: "Disabled", disabled: true },
  ] };
  const { rerender } = render(<Edit value={["vat"]} field={field} onChange={onChange} />);
  const trigger = screen.getByRole("combobox", { name: "Taxes" });
  expect(within(trigger).getByText("VAT 20%")).toBeTruthy();
  fireEvent.click(trigger);
  choose(await screen.findByRole("option", { name: "Service 5%" }));
  expect(onChange).toHaveBeenLastCalledWith(["vat", "fee"]);
  rerender(<Edit value={["vat", "fee"]} field={field} onChange={onChange} />);
  expect(within(trigger).getByText("+1")).toBeTruthy();
  choose(screen.getByRole("option", { name: "VAT 20%" }));
  expect(onChange).toHaveBeenLastCalledWith(["fee"]);
  rerender(<Edit value={["legacy"]} field={field} onChange={onChange} />);
  choose(await screen.findByRole("option", { name: "legacy" }));
  expect(onChange).toHaveBeenLastCalledWith([]);
  cleanup();
});

test("a read-only multiple relation has no picker", () => {
  const Edit = many2manyWidget.edit;
  render(<Edit value={["vat"]} field={{ options: [{ value: "vat", label: "VAT 20%" }] }} readOnly />);
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.getByText("VAT 20%")).toBeTruthy();
  cleanup();
});


test("full forms retain individually removable chips, including unloaded selections", () => {
  const Edit = many2manyWidget.edit;
  const onChange = vi.fn();
  render(<Edit value={["known", "unloaded"]} field={{ options: [{ value: "known", label: "Known" }] }} onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: /unloaded/ }));
  expect(onChange).toHaveBeenLastCalledWith(["known"]);
  expect(screen.queryByText("+1")).toBeNull();
});
