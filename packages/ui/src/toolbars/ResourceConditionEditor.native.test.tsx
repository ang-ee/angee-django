// @vitest-environment happy-dom
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../testing";
import { ResourceConditionEditor } from "./ResourceConditionEditor";

const providers = createUiTestProviders({
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); providers.clearClients(); });

const channel = testDataResource("demo.Channel", {
  recordRepresentation: "display_name",
  query: testResourceQuery({ fields: {
    display_name: testQueryField("display_name", { filter: { field: "display_name", scalar: "String", values: [], operators: ["iContains"] } }),
  } }),
});
const message = testDataResource("demo.Message", {
  fields: [{ name: "channel", kind: "relation", scalar: "ID", relationModelLabel: "demo.Channel", relationObject: false,
    readable: true, creatable: true, updatable: true, requiredOnCreate: false, aggregatable: false }],
  query: testResourceQuery({ fields: {
    channel: testQueryField("channel", { kind: "relation", scalar: "ID", relation: { model: "demo.Channel" },
      filter: { field: "channel_id", scalar: "ID", values: [], operators: ["exact", "ne", "isNull"] } }),
  } }),
});
const scalarRecord = testDataResource("demo.Record", {
  query: testResourceQuery({ fields: {
    status: testQueryField("status", { kind: "enum", values: [{ value: "IN_REVIEW", description: "In review" }], filter: {
      field: "status", scalar: "String", values: [], operators: ["exact"], valueMap: [{ from: "IN_REVIEW", to: "in_review" }],
    } }),
    priority: testQueryField("priority", { kind: "enum", values: [
      { value: "LOW", description: "Low priority" }, { value: "HIGH", description: "High priority" },
    ], filter: { field: "priority", scalar: "Int", values: [], operators: ["exact"],
      valueMap: [{ from: "LOW", to: 0 }, { from: "HIGH", to: 5 }],
    } }),
    reviewed: testQueryField("reviewed", { kind: "enum", values: [{ value: "NO", description: "Not reviewed" }], filter: {
      field: "reviewed", scalar: "Boolean", values: [], operators: ["exact"], valueMap: [{ from: "NO", to: false }],
    } }),
    amount: testQueryField("amount", { scalar: "Decimal", filter: { field: "amount", scalar: "Decimal", values: [], operators: ["exact"] } }),
    happened_at: testQueryField("happened_at", { scalar: "DateTime", filter: { field: "happened_at", scalar: "DateTime", values: [], operators: ["exact"] } }),
    metadata: testQueryField("metadata", { kind: "json", scalar: "JSON", filter: { field: "metadata", scalar: "JSON", values: [], operators: ["exact"] } }),
    title: testQueryField("title", { filter: { field: "title", scalar: "String", values: [], operators: ["exact"] } }),
  } }),
});

function scalarFixture(value: Record<string, unknown>, readOnly = false) {
  const onChange = vi.fn();
  render(<providers.Provider resources={[scalarRecord]}>
    <ResourceConditionEditor resource="demo.Record" value={value} readOnly={readOnly} onChange={onChange} />
  </providers.Provider>);
  return onChange;
}

test("mapped enum choices display their retained label and unchanged Apply retains the filter input value", () => {
  const condition = { status: { _eq: "in_review" } };
  const onChange = scalarFixture(condition);
  expect(screen.getAllByRole("combobox", { name: "Filter value" })[0]!.textContent).toBe("In review");
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

test("read-only mapped enum choices retain their label without allowing edits", () => {
  const onChange = scalarFixture({ status: { _eq: "in_review" } }, true);
  const operand = screen.getByRole("combobox", { name: "Filter value" });
  expect(operand.textContent).toBe("In review");
  expect(operand.getAttribute("aria-readonly")).toBe("true");
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
});

test("IntegerChoices retain human labels and numeric filter inputs when kept or changed", async () => {
  const condition = { priority: { _eq: 0 } };
  const onChange = scalarFixture(condition);
  const operand = screen.getAllByRole("combobox", { name: "Filter value" })[0]!;
  expect(operand.textContent).toBe("Low priority");
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenLastCalledWith(condition);
  fireEvent.click(operand);
  const high = await screen.findByRole("option", { name: "High priority" });
  fireEvent.pointerDown(high, { pointerType: "mouse" });
  fireEvent.click(high);
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenLastCalledWith({ priority: { _eq: 5 } });
});

test("Boolean enum choices submit their typed false input instead of the native select token", () => {
  const condition = { reviewed: { _eq: false } };
  const onChange = scalarFixture(condition);
  expect(screen.getAllByRole("combobox", { name: "Filter value" })[0]!.textContent).toBe("Not reviewed");
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

test("unchanged Decimal comparisons preserve every digit", () => {
  const condition = { amount: { _eq: "9007199254740993.25" } };
  const onChange = scalarFixture(condition);
  const operand = screen.getByRole("spinbutton", { name: "Filter value" }) as HTMLInputElement;
  expect(operand.value).toBe("9007199254740993.25");
  expect(operand.checkValidity()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

test("the native datetime widget displays an aware timestamp and unchanged Apply retains timezone and precision", () => {
  const condition = { happened_at: { _eq: "2026-10-23T12:34:56.123456+05:30" } };
  const onChange = scalarFixture(condition);
  const operand = screen.getByRole("button", { name: "Filter value" });
  expect(operand.textContent).not.toBe("Filter value");
  expect(operand.textContent).not.toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

test.each([{ retained: ["member", 1] }, "retained JSON string"])("unchanged exact JSON operands preserve their type: %j", (operand) => {
  const condition = { metadata: { _eq: operand } };
  const onChange = scalarFixture(condition);
  expect((screen.getByRole("textbox", { name: "Filter value" }) as HTMLInputElement).value).toBe(JSON.stringify(operand));
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

test("unchanged text comparisons preserve meaningful surrounding spaces", () => {
  const condition = { title: { _eq: "  Retained  " } };
  const onChange = scalarFixture(condition);
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

test.each(["", "   "])("retained String equality preserves blank input %j as distinct from null", (value) => {
  const condition = { title: { _eq: value } };
  const onChange = scalarFixture(condition);
  expect((screen.getByRole("textbox", { name: "Filter value" }) as HTMLInputElement).value).toBe(value);
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith(condition);
});

function fixture(initial: Record<string, unknown>, readOnly = false) {
  const onChange = vi.fn();
  const getOne = vi.fn(async ({ id }: { id: string | number }) => ({ data: {
    id, display_name: id === "channel-999" ? "Distant channel" : id === "channel-first" ? "First channel" : "Other channel",
  } }));
  const getList = vi.fn(async ({ filters }: { filters?: unknown[] }) => ({
    data: JSON.stringify(filters).includes("Other")
      ? [{ id: "channel-next", display_name: "Other channel" }]
      : [{ id: "channel-first", display_name: "First channel" }],
    total: 1000,
  }));
  function Condition() {
    const [value, setValue] = useState(initial);
    return <ResourceConditionEditor resource="demo.Message" value={value} readOnly={readOnly}
      onChange={(next) => { onChange(next); setValue(next); }} />;
  }
  render(<providers.Provider resources={[message, channel]} dataProvider={{ getOne, getList }}>
    <Condition />
  </providers.Provider>);
  return { onChange, getOne, getList };
}

test("retained relation clauses resolve their label and use remote search before applying a public ID", async () => {
  const { onChange, getOne, getList } = fixture({ channel_id: { _eq: "channel-999" } });
  const picker = await screen.findByRole("combobox", { name: "Filter value: Distant channel" });
  expect(getOne).toHaveBeenCalledWith(expect.objectContaining({ resource: "channels", id: "channel-999" }));
  expect(getList).not.toHaveBeenCalled();
  fireEvent.click(picker);
  await screen.findByRole("option", { name: "First channel" });
  expect(screen.getByRole("option", { name: "Distant channel" })).toBeTruthy();
  fireEvent.change(screen.getByPlaceholderText("Search…"), { target: { value: "Other" } });
  const other = await screen.findByRole("option", { name: "Other channel" });
  expect(getList).toHaveBeenLastCalledWith(expect.objectContaining({
    resource: "channels", filters: [{ operator: "or", value: [{ field: "display_name", operator: "contains", value: "Other" }] }],
  }));
  fireEvent.click(other);
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  expect(onChange).toHaveBeenLastCalledWith({ channel_id: { _eq: "channel-next" } });
  await screen.findByRole("combobox", { name: "Filter value: Other channel" });
});

test("new relation clauses use the same lazy picker and persist the query-owned wire path", async () => {
  const { onChange, getOne, getList } = fixture({});
  expect(getOne).not.toHaveBeenCalled();
  expect(getList).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("combobox", { name: "Filter value" }));
  fireEvent.click(await screen.findByRole("option", { name: "First channel" }));
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onChange).toHaveBeenLastCalledWith({ channel_id: { _eq: "channel-first" } });
  await screen.findByRole("combobox", { name: "Filter value: First channel" });
});

test("read-only retained relations resolve labels without loading or opening an option list", async () => {
  const { onChange, getList } = fixture({ channel_id: { _eq: "channel-999" } }, true);
  const picker = await screen.findByRole("combobox", { name: "Filter value: Distant channel" });
  expect((picker as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(picker);
  expect(screen.queryByPlaceholderText("Search…")).toBeNull();
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Remove rule" })).toBeNull();
  expect(getList).not.toHaveBeenCalled();
  expect(onChange).not.toHaveBeenCalled();
});

test("editing one comparison preserves another comparison on the same relation", async () => {
  const { onChange } = fixture({ channel_id: { _eq: "channel-999", _neq: "channel-next" } });
  fireEvent.click(await screen.findByRole("combobox", { name: "Filter value: Distant channel" }));
  fireEvent.click(await screen.findByRole("option", { name: "First channel" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Apply" })[0]!);
  expect(onChange).toHaveBeenLastCalledWith({ _and: [
    { channel_id: { _neq: "channel-next" } }, { channel_id: { _eq: "channel-first" } },
  ] });
});

test("editing an operator preserves negated groups and null checks round-trip without an operand", async () => {
  const { onChange } = fixture({ _not: { _or: [{ channel_id: { _eq: "channel-999" } }, { channel_id: { _is_null: false } }] } });
  await screen.findByRole("combobox", { name: "Filter value: Distant channel" });
  const operators = screen.getAllByRole("combobox", { name: "Filter operator" });
  expect(operators.some((operator) => operator.textContent?.includes("is not empty"))).toBe(true);
  fireEvent.click(operators[0]!);
  const not = await screen.findByRole("option", { name: "is not" });
  fireEvent.pointerDown(not, { pointerType: "mouse" });
  fireEvent.click(not);
  fireEvent.click(screen.getAllByRole("button", { name: "Apply" })[0]!);
  expect(onChange).toHaveBeenLastCalledWith({ _not: { _or: [{ channel_id: { _neq: "channel-999" } }, { channel_id: { _is_null: false } }] } });
  fireEvent.click(screen.getAllByRole("button", { name: "Apply" })[1]!);
  expect(onChange).toHaveBeenLastCalledWith({ _not: { _or: [{ channel_id: { _neq: "channel-999" } }, { channel_id: { _is_null: false } }] } });
});
