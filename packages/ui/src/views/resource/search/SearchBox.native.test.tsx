// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource, testQueryField, testQueryAxis, testResourceQuery } from "@angee/metadata/testing";
import { createUiTestProviders } from "../../../testing";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useSearchCatalog } from "./catalog";
import { useResourceSearch } from "./use-resource-search";
import { SearchBox } from "./SearchBox";
import { searchFixture } from "./search-fixture.test-support";

const providers = createUiTestProviders();
afterEach(() => { cleanup(); providers.clearClients(); });
test("relation suggestions use server search beyond the facet page and remain clearable facet chips", async () => {
  const reviewer = testDataResource("test.Reviewer", { recordRepresentation: "name", query: testResourceQuery({ fields: {
    name: testQueryField("name", { filter: { field: "name", scalar: "String", values: [], operators: ["exact", "iContains"] } }),
  } }) });
  const record = testDataResource("test.Record", { query: testResourceQuery({ fields: {
    title: testQueryField("title", { filter: { field: "title", scalar: "String", values: [], operators: ["iContains"] } }),
    reviewer: testQueryField("reviewer", { kind: "relation", scalar: "ID", relation: { model: "test.Reviewer" },
      filter: { field: "reviewer", scalar: "ID", values: [], operators: ["exact", "inList", "isNull"] } }),
  }, axes: { reviewer: testQueryAxis("reviewer", { kind: "relation", server: { input: "reviewer_id", key: "reviewerId" },
    drill: { kind: "identity", field: "reviewer", valueKey: "reviewerId", valueMap: [], nullMode: "isNull" } }) } }) });
  const getList = vi.fn(async () => ({ data: [{ id: "remote-301", name: "Abby" }], total: 1 }));
  const query = ResourceQuery.from(record);
  function Content() {
    const resourceView = useResourceView();
    const catalog = useSearchCatalog({ resourceView, query, columns: [{ field: "title" }], rows: [], modelMetadata: null,
      textFilterField: "title", serverGrouping: true, declaredFacets: [{ field: "reviewer", label: "Reviewer", source: "relation", options: [
        { id: "first-page", label: "First page only", value: "first", filter: { reviewer: { exact: "first" } } },
      ] }] });
    return <SearchBox search={useResourceSearch({ resourceView, catalog })} />;
  }
  render(<providers.Provider resources={[record, reviewer]} dataProvider={{
    getList, getOne: vi.fn(async () => ({ data: { id: "remote-301", name: "Abby" } })),
  }}><ResourceViewProvider scope="local" resource="test.Record"><Content /></ResourceViewProvider></providers.Provider>);
  const input = screen.getByRole("combobox", { name: "Filter records" });
  fireEvent.input(input, { target: { value: "ab" }, inputType: "insertText" });
  const suggestion = await screen.findByRole("option", { name: "Reviewer: Abby" });
  expect(screen.queryByRole("option", { name: /First page only/ })).toBeNull();
  expect(getList).toHaveBeenCalledWith(expect.objectContaining({ resource: "reviewers", filters: [
    { operator: "or", value: [{ field: "name", operator: "contains", value: "ab" }] },
  ] }));
  fireEvent.pointerDown(suggestion, { pointerType: "mouse" }); fireEvent.click(suggestion);
  await waitFor(() => expect(screen.getByRole("toolbar", { name: "Active search" }).textContent).toContain("Reviewer: Abby"));
  expect(screen.getByRole("toolbar", { name: "Active search" }).querySelectorAll("button")).toHaveLength(1);
  fireEvent.click(screen.getByRole("toolbar", { name: "Active search" }).querySelector("button")!);
  await waitFor(() => expect(screen.queryByRole("toolbar", { name: "Active search" })).toBeNull());
});

test.each([
  { text: "AL", labels: ["Reviewer: alice"] },
  { text: "zzzzqq", labels: [] },
])("relation suggestions match loaded labels for a non-searchable target ($text)", async ({ text, labels }) => {
  const reviewer = testDataResource("test.Reviewer", {
    recordRepresentation: "display_name",
    query: testResourceQuery({ fields: {
      display_name: testQueryField("display_name", { filter: null }),
    } }),
  });
  const rows = [
    { id: "rev_admin", display_name: "admin" },
    { id: "rev_alice", display_name: "alice" },
    { id: "rev_bob", display_name: "bob" },
  ];
  let completeRead = () => {};
  const read = new Promise<{ data: typeof rows; total: number }>((resolve) => {
    completeRead = () => resolve({ data: rows, total: rows.length });
  });
  const getList = vi.fn(() => read);
  const search = searchFixture({ catalog: { facets: [{
    field: "reviewer", label: "Reviewer", source: "relation", options: [],
    relation: { resource: reviewer.modelLabel, labelField: "display_name", canCreate: false },
    optionForValue: (value, label) => ({ id: value, value, label, filter: { reviewer: { exact: value } } }),
  }] } });
  render(<providers.Provider resources={[reviewer]} dataProvider={{ getList }}><SearchBox search={search} /></providers.Provider>);
  fireEvent.input(screen.getByRole("combobox", { name: "Filter records" }), { target: { value: text }, inputType: "insertText" });
  await waitFor(() => expect(getList).toHaveBeenCalledWith(expect.objectContaining({
    resource: "reviewers", filters: [],
    pagination: { mode: "server", currentPage: 1, pageSize: 200 },
    meta: expect.objectContaining({ fields: ["id", "display_name"] }),
  })));
  await act(async () => { completeRead(); await read; });
  await waitFor(() => expect(providers.clients[0]?.isFetching()).toBe(0));
  expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual([
    `Search Title for: ${text}`, ...labels,
  ]);
});
