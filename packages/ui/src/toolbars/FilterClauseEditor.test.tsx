// @vitest-environment happy-dom
import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../testing";
import { RelationPicker } from "../views/relation/RelationPicker";
import { FilterClauseEditor, FilterClauseRow, type FilterClauseDraft, type FilterClauseField } from "./FilterClauseEditor";

const { Provider } = createUiTestProviders();
const fields: readonly FilterClauseField[] = [
  { id: "name", label: "Name", type: "text", operators: ["iContains", "exact", "isNull"] },
  { id: "count", field: "item_count", label: "Count", type: "number", operators: ["gte", "exact"] },
];
afterEach(cleanup);
async function choose(label: string, option: string) {
  fireEvent.click(screen.getByLabelText(label));
  const item = await screen.findByRole("option", { name: option });
  fireEvent.pointerDown(item, { pointerType: "mouse" });
  fireEvent.click(item);
}

test("the controlled row reports field and operator changes without private clause state", async () => {
  const onChange = vi.fn();
  const value: FilterClauseDraft = { fieldId: "name", operator: "iContains", value: "alpha" };
  const view = render(<FilterClauseRow fields={fields} value={value} onChange={onChange} />, { wrapper: Provider });
  await choose("Filter field", "Count");
  expect(onChange).toHaveBeenLastCalledWith({ fieldId: "count", operator: "exact", value: "" });
  expect(screen.getByLabelText("Filter field").textContent).toContain("Name");
  view.rerender(<FilterClauseRow fields={fields} value={{ ...value, operator: "isNull" }} onChange={onChange} />);
  expect(screen.queryByLabelText("Filter value")).toBeNull();
});

test("submits typed values from the keyboard and preserves zero", async () => {
  const onSubmit = vi.fn();
  render(<FilterClauseEditor fields={fields} onSubmit={onSubmit} />, { wrapper: Provider });
  await choose("Filter field", "Count");
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "0" } });
  fireEvent.keyDown(screen.getByLabelText("Filter value"), { key: "Enter" });
  expect(onSubmit).toHaveBeenCalledWith({ field: "item_count", operator: "exact", value: 0, type: "number" });
  expect((screen.getByLabelText("Filter value") as HTMLInputElement).value).toBe("");
});

test("controlled editing retains drafts and leaves collection updates to the caller", () => {
  const onSubmit = vi.fn();
  const onChange = vi.fn();
  const draft: FilterClauseDraft = { fieldId: "name", operator: "exact", value: "alpha" };
  const view = render(<FilterClauseEditor fields={fields} value={draft} onChange={onChange} onSubmit={onSubmit} submitLabel="Apply" />, { wrapper: Provider });
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "beta" } });
  expect(onChange).toHaveBeenCalledWith({ ...draft, value: "beta" });
  view.rerender(<FilterClauseEditor fields={fields} value={{ ...draft, value: "beta" }} onChange={onChange} onSubmit={onSubmit} submitLabel="Apply" />);
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onSubmit).toHaveBeenCalledWith({ field: "name", operator: "exact", value: "beta", type: "text" });
  expect((screen.getByLabelText("Filter value") as HTMLInputElement).value).toBe("beta");
});

test("JSON feedback is linked to the input and invalid drafts never submit", () => {
  const onSubmit = vi.fn();
  render(<FilterClauseEditor fields={[{ id: "tags", label: "Tags", operators: ["inList"] }]} onSubmit={onSubmit} />, { wrapper: Provider });
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "[" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onSubmit).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Filter value").getAttribute("aria-describedby")).toBe(screen.getByRole("alert").id);
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "[]" } });
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onSubmit).toHaveBeenCalledWith({ field: "tags", operator: "inList", value: [] });
});

test("controlled JSON errors survive equal draft replacements and clear when the value changes", () => {
  const fields: readonly FilterClauseField[] = [{ id: "tags", label: "Tags", operators: ["inList"] }];
  const value: FilterClauseDraft = { fieldId: "tags", operator: "inList", value: "[" };
  const onSubmit = vi.fn();
  const view = render(<FilterClauseEditor fields={fields} value={value} onSubmit={onSubmit} />, { wrapper: Provider });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  view.rerender(<FilterClauseEditor fields={fields} value={{ ...value }} onSubmit={onSubmit} />);
  expect(screen.getByRole("alert").textContent).toBe("Enter valid JSON.");
  view.rerender(<FilterClauseEditor fields={fields} value={{ ...value, value: "[]" }} onSubmit={onSubmit} />);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(onSubmit).not.toHaveBeenCalled();
});

test("Enter in the real relation picker selects an option without submitting a clause", async () => {
  const onSubmit = vi.fn();
  function Example() {
    const [value, onChange] = useState<FilterClauseDraft>({ fieldId: "target", operator: "exact", value: "alpha" });
    const fields: readonly FilterClauseField[] = [{
      id: "target", label: "Target", operators: ["exact"],
      renderValue: ({ onValueChange, ...props }) => <RelationPicker {...props} onChange={onValueChange}
        options={[{ value: "alpha", label: "Alpha" }, { value: "beta", label: "Beta" }]} />,
    }];
    return <FilterClauseEditor fields={fields} value={value} onChange={onChange} onSubmit={onSubmit} />;
  }
  render(<Example />, { wrapper: Provider });
  fireEvent.click(screen.getByRole("button", { name: "Filter value: Alpha" }));
  const input = await screen.findByPlaceholderText("Search…");
  fireEvent.change(input, { target: { value: "Beta" } });
  await screen.findByRole("option", { name: "Beta" });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Filter value: Beta" })).toBeTruthy());
  expect(onSubmit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ field: "target", operator: "exact", value: "beta" });
});

test("null operators submit no operand and changed catalogs derive the supported operator", () => {
  const onSubmit = vi.fn();
  const view = render(<FilterClauseEditor fields={fields} onSubmit={onSubmit} />, { wrapper: Provider });
  view.rerender(<FilterClauseEditor fields={[{ id: "other", label: "Other", operators: ["isNotNull"] }]} onSubmit={onSubmit} />);
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onSubmit).toHaveBeenCalledWith({ field: "other", operator: "isNotNull" });
  expect(screen.queryByLabelText("Filter value")).toBeNull();
});

test("read-only mode composes native controls and a declared picker receives its accessible contract", () => {
  const picker = vi.fn(({ value, ...props }: Parameters<NonNullable<FilterClauseField["renderValue"]>>[0]) =>
    <input value={value} readOnly={props.readOnly} aria-label={props["aria-label"]} />);
  render(<FilterClauseEditor fields={[{ id: "target", label: "Target", renderValue: picker }]}
    value={{ fieldId: "target", operator: "exact", value: "alpha" }} onSubmit={vi.fn()} readOnly />, { wrapper: Provider });
  expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
  expect((screen.getByLabelText("Filter value") as HTMLInputElement).readOnly).toBe(true);
  expect(picker).toHaveBeenCalledWith(expect.objectContaining({ readOnly: true, "aria-label": "Filter value" }));
});

test("empty catalogs offer no submit action", () => {
  render(<FilterClauseEditor fields={[]} onSubmit={vi.fn()} />, { wrapper: Provider });
  expect(screen.getByText("No filter fields")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Add" })).toBeNull();
});
