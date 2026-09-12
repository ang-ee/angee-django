// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Refine, type DataProvider } from "@refinedev/core";
import { QueryClient } from "@tanstack/react-query";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { afterEach, expect, test, vi } from "vitest";
import { RelationFieldWidget } from "./RelationFieldWidget";
import { useRelationSelectedOption } from "./relation-options";
import { customFilterChipsFor } from "../resource/resource-view-utils";

const clients: QueryClient[] = [];
afterEach(() => { cleanup(); clients.splice(0).forEach(client => client.clear()); });

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
  const selected = { id: "address-999", name: "Distant sender" };
  const getOne = vi.fn(async () => ({ data: selected }));
  const getList = vi.fn(async ({ filters }: { filters?: unknown[] }) => ({
    data: JSON.stringify(filters).includes("distant@example.com")
      ? [selected]
      : Array.from({ length: 200 }, (_, index) => ({ id: `address-${index}`, name: `Sender ${index}` })),
    total: 1000,
  }));
  const provider = { getApiUrl: () => "test://relations", getOne, getList, create: vi.fn(), update: vi.fn(), deleteOne: vi.fn() } as unknown as DataProvider;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(client);
  const change = vi.fn();
  render(<Refine dataProvider={{ default: provider, console: provider }} options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}>
    <ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([testDataResource("contacts.Address")])}>
      <RelationFieldWidget value={selected.id} onChange={change} relation={{ resource: "contacts.Address", labelField: "name", canCreate: false }} searchFields={["name", "value"]} aria-label="Exact address" />
      <SelectedFilter value={selected.id} />
    </ModelMetadataProvider>
  </Refine>);
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

test("resolves a preset relation's label instead of showing its id", async () => {
  // A create dialog's preset holds only an id, so the form hands the widget a
  // placeholder option labelled with that id. Treating that as a resolved label
  // is what showed `prj_…` / `grp_…` / `stg_…` in every demo create.
  const record = { id: "prj_7", name: "Signage refresh" };
  const getOne = vi.fn(async () => ({ data: record }));
  const getList = vi.fn(async () => ({ data: [record], total: 1 }));
  const provider = {
    getApiUrl: () => "test://relations",
    getOne, getList, create: vi.fn(), update: vi.fn(), deleteOne: vi.fn(),
  } as unknown as DataProvider;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  clients.push(client);

  render(
    <Refine
      dataProvider={{ default: provider, console: provider }}
      options={{ disableTelemetry: true, reactQuery: { clientConfig: client } }}
    >
      <ModelMetadataProvider
        metadata={schemaFieldMetadataFromDataResources([
          testDataResource("projects.Project"),
        ])}
      >
        <RelationFieldWidget
          value="prj_7"
          selectedOption={{ value: "prj_7", label: "prj_7" }}
          onChange={vi.fn()}
          relation={{ resource: "projects.Project", labelField: "name", canCreate: false }}
          aria-label="Project"
        />
      </ModelMetadataProvider>
    </Refine>,
  );

  // The id holds the trigger while the record read is in flight -- an id beats
  // an empty control -- and the real label replaces it once resolved.
  await screen.findByText("Signage refresh");
  expect(screen.queryByText("prj_7")).toBeNull();
  expect(getOne).toHaveBeenCalledOnce();
});
