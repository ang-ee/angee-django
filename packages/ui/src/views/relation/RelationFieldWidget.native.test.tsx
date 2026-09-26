// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { RelationFieldWidget } from "./RelationFieldWidget";
import { useRelationSelectedOption } from "./relation-options";
import { customFilterChipsFor } from "../resource/resource-view-utils";

const { Provider, clearClients } = createUiTestProviders({
  apiUrl: "test://relations",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

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
  fireEvent.click(screen.getByRole("button", { name: "Exact address: Distant sender" }));
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
