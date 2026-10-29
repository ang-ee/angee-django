// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { AppRuntimeProvider, createRouteHref } from "../../runtime";
import { RelationFieldWidget } from "./RelationFieldWidget";
import { RelationField } from "../../widgets/RelationField";
import { useRelationSelectedOption } from "./relation-options";
import { customFilterChipsFor } from "../resource/resource-view-utils";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://relations",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

test("read-only relation values use their retained label and record route without picker queries", () => {
  const getOne = vi.fn();
  const getList = vi.fn();
  const resource = testDataResource("contacts.Address", { recordRepresentation: "name" });
  const runtime = {
    routeHref: createRouteHref([{ name: "addresses", path: "/addresses" }, { name: "address.record", path: "/addresses/$id" }]),
    routesByResource: { "contacts.Address": { collection: "addresses", record: { name: "address.record", param: "id" } } },
  };
  render(<Provider resources={[resource]} dataProvider={{ getOne, getList }}>
    <AppRuntimeProvider runtime={runtime}><RelationFieldWidget readOnly value="address-1"
      selectedOption={{ value: "address-1", label: "Retained sender" }}
      relation={{ resource: "contacts.Address", labelField: "name", canCreate: true }} />
    </AppRuntimeProvider>
  </Provider>);
  expect(screen.getByRole("link", { name: "Retained sender" }).getAttribute("href")).toBe("/addresses/address-1");
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(getOne).not.toHaveBeenCalled();
  expect(getList).not.toHaveBeenCalled();
});

test("empty read-only relations render no editable placeholder and unrouted values retain their label", () => {
  const getOne = vi.fn();
  const getList = vi.fn();
  const relation = { resource: "contacts.Address", labelField: "name", canCreate: false };
  const { rerender } = render(<Provider dataProvider={{ getOne, getList }}>
    <RelationFieldWidget readOnly value={null} relation={relation} placeholder="Choose a sender" />
  </Provider>);
  expect(screen.queryByText("Choose a sender")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  rerender(<Provider resources={[testDataResource("contacts.Address")]} dataProvider={{ getOne, getList }}>
    <RelationFieldWidget readOnly value="address-1" relation={relation}
      selectedOption={{ value: "address-1", label: "Retained sender" }} />
  </Provider>);
  expect(screen.getByText("Retained sender")).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
  expect(getOne).not.toHaveBeenCalled();
  expect(getList).not.toHaveBeenCalled();
});

function SelectedFilter({ value }: { value: string }) {
  const selected = useRelationSelectedOption(
    { resource: "contacts.Address", labelField: "name", canCreate: false },
    value,
  );
  const chips = customFilterChipsFor({ sender: { exact: value } }, [], [{
    id: "sender", label: "Sender", options: selected ? [selected] : [],
  }]);
  return <span>{chips[0]?.label}</span>;
}

test("relation search reaches records beyond the first page and resolves a selected label separately", async () => {
  const searchFields = ["name", "value"];
  const resource = testDataResource("contacts.Address", {
    query: testResourceQuery({
      fields: Object.fromEntries(searchFields.map((field) => [field, testQueryField(field, {
        filter: { field, scalar: "String", values: [], operators: ["exact", "iContains"] },
      })])),
    }),
  });
  const selected = { id: "address-999", name: "Distant sender" };
  const getOne = vi.fn(async () => ({ data: selected }));
  const getList = vi.fn(async ({ filters }: { filters?: unknown[] }) => ({
    data: JSON.stringify(filters).includes("distant@example.com")
      ? [selected]
      : Array.from({ length: 200 }, (_, index) => ({ id: `address-${index}`, name: `Sender ${index}` })),
    total: 1000,
  }));
  const change = vi.fn();
  render(<Provider resources={[resource]} refineResources={[]} dataProvider={{ getOne, getList }}>
    <RelationFieldWidget value={selected.id} onChange={change} relation={{ resource: "contacts.Address", labelField: "name", canCreate: false }} searchFields={searchFields} aria-label="Exact address" />
    <SelectedFilter value={selected.id} />
  </Provider>);
  await screen.findByText("Distant sender");
  await screen.findByText("Sender is Distant sender");
  expect(getOne).toHaveBeenCalledOnce();
  expect(getList).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("combobox", { name: "Exact address: Distant sender" }));
  await screen.findByRole("option", { name: "Sender 199" });
  fireEvent.change(screen.getByPlaceholderText("Search…"), { target: { value: "distant@example.com" } });
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].filters).toEqual([{
    operator: "or", value: [{ field: "name", operator: "contains", value: "distant@example.com" }, { field: "value", operator: "contains", value: "distant@example.com" }],
  }]));
  const match = await screen.findByRole("option", { name: "Distant sender" });
  expect(screen.queryByRole("option", { name: "Sender 199" })).toBeNull();
  fireEvent.click(match);
  expect(change).toHaveBeenCalledWith(selected.id);
});


test("single relation combobox retains missing values and offers retry before creation", async () => {
  const retry = vi.fn();
  const create = vi.fn();
  const search = vi.fn();
  const changed = vi.fn();
  const props = {
    value: "unloaded-id", options: [], onCreate: create, onChange: changed,
    onSearchChange: search, "aria-label": "Related row",
  };
  const { rerender } = render(<Provider><RelationField {...props}
    searchState={{ error: "Unavailable", retry }} /></Provider>);
  const trigger = screen.getByRole("combobox", { name: "Related row" });
  expect(trigger.textContent).toContain("unloaded-id");
  fireEvent.click(trigger);
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("Unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
  fireEvent.change(screen.getByPlaceholderText("Search…"), { target: { value: "  New row  " } });
  expect(search).toHaveBeenCalledWith("  New row  ");
  expect(screen.queryByRole("option")).toBeNull();
  rerender(<Provider><RelationField {...props} /></Provider>);
  fireEvent.click(await screen.findByRole("option", { name: /Create.*New row/ }));
  expect(create).toHaveBeenCalledWith("New row");
  expect(changed).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
});


test("a missing relation honors its authored placeholder while retaining its stored id", () => {
  const change = vi.fn();
  const { rerender } = render(<Provider><RelationField value="unloaded-id" options={[]}
    placeholder="Unavailable account" aria-label="Account" onChange={change} /></Provider>);
  const trigger = screen.getByRole("combobox", { name: "Account" });
  expect(trigger.textContent).toContain("Unavailable account");
  expect(trigger.textContent).not.toContain("unloaded-id");
  expect(change).not.toHaveBeenCalled();
  rerender(<Provider><RelationField value="unloaded-id" options={[]}
    aria-label="Account" onChange={change} /></Provider>);
  expect(trigger.textContent).toContain("unloaded-id");
  expect(change).not.toHaveBeenCalled();
});
