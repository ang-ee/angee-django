// @vitest-environment happy-dom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { ResourceQuery } from "@angee/metadata";
import { testDataResource, testQueryField, testQueryAxis, testResourceQuery } from "@angee/metadata/testing";
import { createUiTestProviders } from "../../../testing";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useSearchCatalog } from "./catalog";
import { useResourceSearch } from "./use-resource-search";
import { SearchBox } from "./SearchBox";

afterEach(cleanup);
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
  const providers = createUiTestProviders({ resources: [record, reviewer], dataProvider: {
    getList, getOne: vi.fn(async () => ({ data: { id: "remote-301", name: "Abby" } })),
  } });
  const query = ResourceQuery.from(record);
  function Content() {
    const resourceView = useResourceView();
    const catalog = useSearchCatalog({ resourceView, query, columns: [{ field: "title" }], rows: [], modelMetadata: null,
      textFilterField: "title", serverGrouping: true, declaredFacets: [{ field: "reviewer", label: "Reviewer", source: "relation", options: [
        { id: "first-page", label: "First page only", value: "first", filter: { reviewer: { exact: "first" } } },
      ] }] });
    return <SearchBox search={useResourceSearch({ resourceView, catalog })} />;
  }
  render(<providers.Provider><ResourceViewProvider scope="local" resource="test.Record"><Content /></ResourceViewProvider></providers.Provider>);
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
