// @vitest-environment happy-dom

import { ResourceQuery } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import type { GetListParams, GetOneParams } from "@refinedev/core";
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../../testing";
import { RelationMultiFieldWidget } from "./RelationMultiFieldWidget";
import {
  useRelationOptions, useRelationPickerOptions, useRelationPickerSearch, useRelationSelectedOptions,
  type RelationOptionsConfig,
} from "./relation-options";

const query = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, name: { scalar: "String" }, is_archived: { scalar: "Boolean" },
  "parent.is_archived": { scalar: "Boolean" },
} }).contract;
const resource = testDataResource("example.Option", {
  recordRepresentation: "name", roots: { aggregate: "options_aggregate" },
  typeNames: { filter: "OptionBoolExp", order: "OptionOrderBy" }, query,
  fields: [{ name: "is_archived", kind: "scalar", scalar: "Boolean", archivable: true,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
});
const relation = { resource: resource.modelLabel, labelField: "name", canCreate: false };
const { Provider, clearClients } = createUiTestProviders({
  resources: [resource], apiUrl: "test://archive-picker",
  queryClientConfig: { defaultOptions: { queries: { retry: false, staleTime: Infinity } } },
});
afterEach(() => { cleanup(); clearClients(); });

test("picker search hides archives while its selected identity still resolves and stays labelled", async () => {
  const getList = vi.fn(async (_request: GetListParams) => ({ data: [{ id: "active", name: "Active" }], total: 1 }));
  const getOne = vi.fn(async (_request: GetOneParams) => ({ data: { id: "archived", name: "Archived selection" } }));
  const { result } = renderHook(() => useRelationPickerOptions(relation, { value: "archived" }), {
    wrapper: ({ children }) => <Provider dataProvider={{ getList, getOne }}>{children}</Provider>,
  });
  await waitFor(() => expect(result.current.options).toContainEqual({ value: "archived", label: "Archived selection" }));
  expect(getList).not.toHaveBeenCalled();
  expect(getOne.mock.calls[0]?.[0].meta?.gqlVariables?.where).toBeUndefined();
  act(() => result.current.onOpenChange(true));
  await waitFor(() => expect(result.current.options).toContainEqual({ value: "active", label: "Active" }));
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({ is_archived: { _eq: false } });
  expect(result.current.options).toContainEqual({ value: "archived", label: "Archived selection" });
  act(() => result.current.onSearchChange("Active"));
  await waitFor(() => expect(getList.mock.calls.at(-1)?.[0].filters).toEqual([
    { operator: "or", value: [{ field: "name", operator: "contains", value: "Active" }] },
  ]));
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual({ is_archived: { _eq: false } });
});

const explicit: RelationOptionsConfig[] = [
  { filters: [{ operator: "or", value: [
    { field: "is_archived", operator: "eq", value: true },
    { field: "name", operator: "contains", value: "One" },
  ] }] },
  { filters: [{ field: "is_archived", operator: "eq", value: false }] },
  { where: { _not: { is_archived: { _eq: false } } } },
];

test.each(explicit)("explicit native or where archive predicates get no added condition: %j", async (config) => {
  const getList = vi.fn(async (_request: GetListParams) => ({ data: [], total: 0 }));
  renderHook(() => useRelationPickerSearch(relation, config), {
    wrapper: ({ children }) => <Provider dataProvider={{ getList }}>{children}</Provider>,
  });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  expect(getList.mock.calls.at(-1)?.[0].filters).toEqual(config.filters ?? []);
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual(config.where);
});

test("a relation's same-named flag does not opt the picker out of its own archive scope", async () => {
  const filters: NonNullable<RelationOptionsConfig["filters"]> = [
    { field: "parent.is_archived", operator: "eq", value: true },
  ];
  const getList = vi.fn(async (_request: GetListParams) => ({ data: [], total: 0 }));
  renderHook(() => useRelationPickerSearch(relation, { filters, where: { name: { _eq: "One" } } }), {
    wrapper: ({ children }) => <Provider dataProvider={{ getList }}>{children}</Provider>,
  });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  expect(getList.mock.calls.at(-1)?.[0].filters).toEqual(filters);
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where)
    .toEqual({ _and: [{ is_archived: { _eq: false } }, { name: { _eq: "One" } }] });
});

test("multi-select searches use the same scope while missing selected labels use an unscoped id read", async () => {
  const getList = vi.fn(async ({ filters }: GetListParams) => {
    const lookup = filters?.some((filter) => "field" in filter && filter.field === "id");
    return { data: lookup ? [{ id: "archived", name: "Archived selection" }] : [{ id: "active", name: "Active" }], total: 1 };
  });
  render(<Provider dataProvider={{ getList }}>
    <RelationMultiFieldWidget value={["archived"]} relation={relation} create={null} aria-label="Options" />
  </Provider>);
  await screen.findByText("Archived selection");
  const requests = getList.mock.calls.map(([request]) => request);
  const lookup = requests.find((request) => request.filters?.some((filter) => "field" in filter && filter.field === "id"));
  expect(lookup?.filters).toEqual([{ field: "id", operator: "in", value: ["archived"] }]);
  expect(lookup?.meta?.gqlVariables?.where).toBeUndefined();
  expect(requests.some((request) => JSON.stringify(request.meta?.gqlVariables?.where)
    === JSON.stringify({ is_archived: { _eq: false } }))).toBe(true);
});

test("catalogue reads and selected-label batches retain archived rows", async () => {
  const getList = vi.fn(async (_request: GetListParams) => ({ data: [{ id: "archived", name: "Archived" }], total: 1 }));
  const { result } = renderHook(() => ({
    catalogue: useRelationOptions(relation),
    selected: useRelationSelectedOptions(relation, ["archived"], []),
  }), { wrapper: ({ children }) => <Provider dataProvider={{ getList }}>{children}</Provider> });
  await waitFor(() => expect(result.current.catalogue.options).toHaveLength(1));
  await waitFor(() => expect(result.current.selected).toEqual([{ value: "archived", label: "Archived" }]));
  expect(getList.mock.calls.every(([request]) => request.meta?.gqlVariables?.where === undefined)).toBe(true);
});

test("a resource without the archive facet keeps its picker request unchanged", async () => {
  const getList = vi.fn(async (_request: GetListParams) => ({ data: [], total: 0 }));
  renderHook(() => useRelationPickerSearch(relation), {
    wrapper: ({ children }) => <Provider resources={[{ ...resource, fields: [] }]} dataProvider={{ getList }}>{children}</Provider>,
  });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toBeUndefined();
});

test("a picker without executable archive filtering preserves its raw condition", async () => {
  const unfilterable = { ...resource, query: { ...query, fields: {
    ...query.fields, is_archived: { ...query.fields.is_archived!, filter: null },
  } } };
  const where = { is_archived: { _eq: true } };
  const getList = vi.fn(async (_request: GetListParams) => ({ data: [], total: 0 }));
  renderHook(() => useRelationPickerSearch(relation, { where }), {
    wrapper: ({ children }) => <Provider resources={[unfilterable]} dataProvider={{ getList }}>{children}</Provider>,
  });
  await waitFor(() => expect(getList).toHaveBeenCalled());
  expect(getList.mock.calls.at(-1)?.[0].meta?.gqlVariables?.where).toEqual(where);
});
