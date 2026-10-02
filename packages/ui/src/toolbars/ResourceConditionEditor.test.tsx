// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../testing";
import { ResourceConditionEditor } from "./ResourceConditionEditor";

const { Provider } = createUiTestProviders();
const resource = testDataResource("demo.Record", { fields: [{
  name: "title", kind: "scalar", scalar: "String", readable: true, creatable: true,
  updatable: true, requiredOnCreate: false, aggregatable: false,
}], query: testResourceQuery({ fields: {
  title: testQueryField("title", { filter: { field: "label", scalar: "String", values: [], operators: ["exact"] } }),
} }) });
const metadata = schemaFieldMetadataFromDataResources([resource]);
afterEach(cleanup);

function fixture(value: unknown, readOnly = false) {
  const onChange = vi.fn();
  render(<Provider><ModelMetadataProvider metadata={metadata}>
    <ResourceConditionEditor resource="demo.Record" value={value} onChange={onChange} readOnly={readOnly} />
  </ModelMetadataProvider></Provider>);
  return onChange;
}

test("new clauses use the resource wire mapping and retain the stored Boolean group", () => {
  const onChange = fixture({});
  fireEvent.change(screen.getByLabelText("Filter value"), { target: { value: "Ready" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(onChange).toHaveBeenCalledWith({ label: { _eq: "Ready" } });
});

test("nested existing conditions are visible and read-only conditions cannot be changed", () => {
  fixture({ _or: [{ label: { _eq: "First" } }, { _not: { label: { _eq: "Second" } } }] }, true);
  expect(screen.getByText("Any of these conditions")).toBeTruthy();
  expect(screen.getByText("Not this condition")).toBeTruthy();
  expect(screen.getByText(/First/)).toBeTruthy();
  expect(screen.getByText(/Second/)).toBeTruthy();
  expect(screen.queryByRole("button")).toBeNull();
});

test("removing a branch preserves the OR group and every other branch", () => {
  const onChange = fixture({ _or: [{ label: { _eq: "First" } }, { label: { _eq: "Second" } }] });
  fireEvent.click(screen.getAllByRole("button", { name: "Remove branch" })[0]!);
  expect(onChange).toHaveBeenCalledWith({ _or: [{ label: { _eq: "Second" } }] });
});

test("unrepresentable stored conditions show a failure without replacing them", () => {
  const onChange = fixture({ hidden: { _eq: "Retained" } });
  expect(screen.getByRole("alert").textContent).toContain("unknown or non-filterable field");
  expect(screen.queryByLabelText("Filter value")).toBeNull();
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Clear condition" }));
  expect(onChange).toHaveBeenCalledExactlyOnceWith({});
});
