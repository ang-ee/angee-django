// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { useState } from "react";

import { createUiTestProviders } from "../testing";
import { SchemaPathPicker, type SchemaPath, type SchemaPathSchema } from "./SchemaPathPicker";

const { Provider } = createUiTestProviders();
afterEach(cleanup);

const schema: SchemaPathSchema = {
  type: "object",
  properties: {
    "a.b/c": { type: "object", properties: { "": { type: "string", title: "Empty key" } } },
    entries: { type: "array", items: { type: "object", properties: { name: { type: "string" } } } },
  },
};

test("selects literal keys and the root with native tree keyboard navigation", () => {
  const change = vi.fn();
  const view = render(<SchemaPathPicker schema={schema} value={null} onChange={change} />, { wrapper: Provider });
  expect(screen.getByRole("tree", { name: "Field path" })).toBeTruthy();
  const root = screen.getByRole("treeitem", { name: /Whole value/ });
  fireEvent.focus(root);
  fireEvent.keyDown(root, { key: "Enter" });
  expect(change).toHaveBeenLastCalledWith([]);
  fireEvent.keyDown(root, { key: "ArrowRight" });
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  fireEvent.keyDown(document.activeElement!, { key: "Enter" });
  expect(change).toHaveBeenLastCalledWith(["a.b/c", ""]);
  view.rerender(<SchemaPathPicker schema={schema} value={["a.b/c", ""]} onChange={change} />);
  expect(screen.getByRole("treeitem", { name: "Empty key" }).getAttribute("aria-selected")).toBe("true");
});

test("uses concrete array indices and rejects negative, fractional and unsafe indices", () => {
  const change = vi.fn();
  render(<SchemaPathPicker schema={schema} value={["entries", 3, "name"]} onChange={change} />, { wrapper: Provider });
  const index = screen.getByRole("textbox", { name: 'Array index for ["entries"]' });
  expect(index.getAttribute("value")).toBe("3");
  fireEvent.click(screen.getByRole("treeitem", { name: "name" }));
  expect(change).toHaveBeenLastCalledWith(["entries", 3, "name"]);
  for (const invalid of ["-1", "1.5", "9007199254740992", ""]) {
    fireEvent.change(index, { target: { value: invalid } });
    expect(screen.queryByRole("treeitem", { name: "name" })).toBeNull();
    expect(index.getAttribute("aria-invalid")).toBe("true");
  }
  fireEvent.change(index, { target: { value: "2" } });
  expect(change).toHaveBeenLastCalledWith(["entries", 2, "name"]);
});

test("combines inline branches and delegates references without recursing forever", () => {
  const recursive: SchemaPathSchema = { properties: { child: { $ref: "#node" } } };
  const resolve = vi.fn((ref: string) => ref === "#node" ? structuredClone(recursive) : undefined);
  render(<SchemaPathPicker schema={{
    allOf: [{ properties: { left: { type: "string" } } }, { properties: { right: true } }],
    properties: { branch: { $ref: "#node" }, opaque: { $ref: "external" }, never: false },
  }} value={null} onChange={vi.fn()} resolveReference={resolve} />, { wrapper: Provider });
  expect(screen.getByRole("treeitem", { name: "left" })).toBeTruthy();
  expect(screen.getByRole("treeitem", { name: "right" })).toBeTruthy();
  fireEvent.focus(screen.getByRole("treeitem", { name: "branch" }));
  fireEvent.keyDown(screen.getByRole("treeitem", { name: "branch" }), { key: "ArrowRight" });
  expect(screen.getAllByRole("treeitem", { name: "child" })).toHaveLength(1);
  expect(screen.getByRole("treeitem", { name: "opaque Unresolved reference" })).toBeTruthy();
  expect(screen.queryByRole("treeitem", { name: "never" })).toBeNull();
  expect(resolve).toHaveBeenCalledWith("#node");
  expect(resolve).toHaveBeenCalledTimes(4);
});

test("labels an untitled empty property key without changing its literal path", () => {
  const change = vi.fn();
  render(<SchemaPathPicker schema={{ properties: { "": { type: "string" } } }}
    value={null} onChange={change} />, { wrapper: Provider });
  fireEvent.click(screen.getByRole("treeitem", { name: "(empty key)" }));
  expect(change).toHaveBeenCalledWith([""]);
});

test("offers unrestricted array items and tuple tails using schema defaults", () => {
  const change = vi.fn();
  const view = render(<SchemaPathPicker schema={{ type: ["array", "null"] }}
    value={null} onChange={change} />, { wrapper: Provider });
  fireEvent.click(screen.getByRole("treeitem", { name: "0" }));
  expect(change).toHaveBeenLastCalledWith([0]);
  view.rerender(<SchemaPathPicker schema={{ prefixItems: [{ title: "First" }] }}
    value={null} onChange={change} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("treeitem", { name: "5" }));
  expect(change).toHaveBeenLastCalledWith([5]);
});

test("an externally selected path replaces a previous array-index draft", () => {
  const props = { schema, onChange: vi.fn() };
  const view = render(<SchemaPathPicker {...props} value={["entries", 1, "name"]} />, { wrapper: Provider });
  fireEvent.change(screen.getByRole("textbox", { name: 'Array index for ["entries"]' }), { target: { value: "2" } });
  view.rerender(<SchemaPathPicker {...props} value={["entries", 5, "name"]} />);
  expect(screen.getByRole("textbox", { name: 'Array index for ["entries"]' }).getAttribute("value")).toBe("5");
  expect(screen.getByRole("treeitem", { name: "name" }).getAttribute("aria-selected")).toBe("true");
});

test("handles tuple indices, changing schemas, empty schemas and disabled selection", () => {
  const change = vi.fn();
  const view = render(<SchemaPathPicker schema={{ prefixItems: [{ title: "First" }, { title: "Second" }], items: false }}
    value={null} onChange={change} disabled />, { wrapper: Provider });
  fireEvent.click(screen.getByRole("treeitem", { name: "First" }));
  expect(change).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox").hasAttribute("disabled")).toBe(true);
  view.rerender(<SchemaPathPicker schema={false} value={null} onChange={change} />);
  expect(screen.queryByRole("treeitem")).toBeNull();
  expect(screen.getByText("No selectable paths.")).toBeTruthy();
  view.rerender(<SchemaPathPicker schema={{}} value={null} onChange={change} />);
  fireEvent.click(screen.getByRole("treeitem", { name: "Whole value" }));
  expect(change).toHaveBeenCalledWith([]);
});

test("resolves local escaped definitions through the form schema owner", () => {
  const change = vi.fn();
  render(<SchemaPathPicker schema={{
    $defs: { "a/b~c": { properties: { name: { type: "string" } } } },
    properties: { entry: { $ref: "#/$defs/a~1b~0c" }, missing: { $ref: "#/$defs/absent" } },
  }} value={["entry", "name"]} onChange={change} />, { wrapper: Provider });
  fireEvent.click(screen.getByRole("treeitem", { name: "name" }));
  expect(change).toHaveBeenCalledWith(["entry", "name"]);
  expect(screen.getByRole("treeitem", { name: "missing Unresolved reference" })).toBeTruthy();
});

test("index edits emit the concrete path while preserving its selected descendant", () => {
  function Controlled() {
    const [value, setValue] = useState<SchemaPath>(["entries", 3, "name"]);
    return <><SchemaPathPicker schema={schema} value={value} onChange={setValue} />
      <output>{JSON.stringify(value)}</output></>;
  }
  render(<Controlled />, { wrapper: Provider });
  fireEvent.change(screen.getByRole("textbox", { name: 'Array index for ["entries"]' }), { target: { value: "7" } });
  expect(screen.getByText('["entries",7,"name"]')).toBeTruthy();
  expect(screen.getByRole("treeitem", { name: "name" }).getAttribute("aria-selected")).toBe("true");
});

test("index and key edits drop unavailable descendants and reject forbidden tuple tails", () => {
  const change = vi.fn();
  const view = render(<SchemaPathPicker schema={{
    prefixItems: [{ properties: { name: true } }, { type: "number" }], items: false,
  }} value={[0, "name"]} onChange={change} />, { wrapper: Provider });
  fireEvent.change(screen.getByRole("textbox", { name: "Array index for Whole value" }), { target: { value: "1" } });
  expect(change).toHaveBeenLastCalledWith([1]);
  change.mockClear();
  fireEvent.change(screen.getByRole("textbox", { name: "Array index for Whole value" }), { target: { value: "5" } });
  expect(change).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Array index for Whole value" }).getAttribute("aria-invalid")).toBe("true");
  view.rerender(<SchemaPathPicker schema={{
    patternProperties: { "^obj$": { properties: { name: true } }, "^num$": { type: "number" } },
    additionalProperties: false,
  }} value={["obj", "name"]} onChange={change} />);
  fireEvent.change(screen.getByRole("textbox", { name: "Object key for Whole value" }), { target: { value: "num" } });
  expect(change).toHaveBeenLastCalledWith(["num"]);
});

test("accepts typed additional and pattern keys without parsing literal key characters", () => {
  const change = vi.fn();
  const view = render(<SchemaPathPicker schema={{ additionalProperties: { type: "string" } }}
    value={null} onChange={change} />, { wrapper: Provider });
  fireEvent.change(screen.getByRole("textbox", { name: "Object key for Whole value" }), { target: { value: "a.b/c" } });
  expect(change).toHaveBeenLastCalledWith(["a.b/c"]);
  expect(screen.getByRole("treeitem", { name: "a.b/c" })).toBeTruthy();
  view.rerender(<SchemaPathPicker schema={{ patternProperties: { "^item_": { type: "string" } }, additionalProperties: false }}
    value={null} onChange={change} />);
  change.mockClear();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "invalid" } });
  expect(change).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox").getAttribute("aria-invalid")).toBe("true");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "item_one" } });
  expect(change).toHaveBeenLastCalledWith(["item_one"]);
});

test("recognizes declared keys alongside patterns and implicit open objects", () => {
  const change = vi.fn();
  const view = render(<SchemaPathPicker schema={{
    properties: { fixed: { type: "string" } },
    patternProperties: { "^extra_": { type: "string" } }, additionalProperties: false,
  }} value={["fixed"]} onChange={change} />, { wrapper: Provider });
  expect(screen.getByRole("textbox").getAttribute("aria-invalid")).not.toBe("true");
  view.rerender(<SchemaPathPicker schema={{ type: "object" }} value={null} onChange={change} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "new_key" } });
  expect(change).toHaveBeenLastCalledWith(["new_key"]);
});

test("builds a wide shared DAG only as branches expand and memoizes equal selections", () => {
  const levels: SchemaPathSchema[] = [{ properties: { leaf: true } }];
  for (let level = 1; level < 8; level += 1) {
    levels.push({ properties: Object.fromEntries(Array.from({ length: 20 }, (_, index) =>
      [`branch_${index}`, { $ref: `level_${level - 1}` }])) });
  }
  const resolve = vi.fn((reference: string) => levels[Number(reference.slice(6))]);
  const props = { schema: levels[7]!, onChange: vi.fn(), resolveReference: resolve };
  const view = render(<SchemaPathPicker {...props} value={[]} />, { wrapper: Provider });
  expect(screen.getAllByRole("treeitem")).toHaveLength(21);
  expect(resolve).toHaveBeenCalledTimes(20);
  view.rerender(<SchemaPathPicker {...props} value={[]} />);
  expect(resolve).toHaveBeenCalledTimes(20);
  fireEvent.focus(screen.getByRole("treeitem", { name: "branch_0" }));
  fireEvent.keyDown(screen.getByRole("treeitem", { name: "branch_0" }), { key: "ArrowRight" });
  expect(screen.getAllByRole("treeitem")).toHaveLength(41);
  expect(resolve.mock.calls.length).toBeLessThan(100);
});
