// @vitest-environment happy-dom
import type { ReactNode } from "react";
import { cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { ResourceQuery, schemaFieldMetadataFromDataResources, schemaFieldMetadataWithVocabulary } from "@angee/metadata";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useSearchCatalog, searchTextFields, type UseSearchCatalogInput } from "./catalog";
import type { SearchFacet } from "./types";
import { developmentMode } from "../../../lib/development-mode";
import { SearchControls } from "./SearchControls";
import { pageSearchShortcuts } from "./shortcuts";
import { searchFixture } from "./search-fixture.test-support";

vi.mock("../../../lib/development-mode", () => ({ developmentMode: vi.fn() }));
beforeEach(() => vi.mocked(developmentMode).mockReturnValue(true));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const query = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, "owner.name": { scalar: "String" }, amount: { scalar: "Int" },
  status: { kind: "enum", values: [{ value: "open" }, { value: "closed" }] },
  owner: { kind: "relation", identityPath: "owner.id", labelPath: "owner.name" },
} });
function catalog(input: Partial<UseSearchCatalogInput<Record<string, unknown>>> = {}) {
  return renderHook(() => useSearchCatalog({
    resourceView: useResourceView(), query, columns: [{ field: "title" }, { field: "status" }, { field: "owner" }],
    rows: [], modelMetadata: null, ...input,
  }), { wrapper: ({ children }: { children: ReactNode }) => <ResourceViewProvider scope="local">{children}</ResourceViewProvider> }).result.current;
}
const status: SearchFacet = { field: "status", label: "Contributed status", source: "scalar", options: [
  { id: "bucket-open", label: "Contributed open", filter: { status: { exact: "open" } }, value: "open" },
  { id: "bucket-blank", label: "Not given", filter: { status: { isNull: true } } },
] };

test("declared choices beat contributions and inference with one executable predicate per choice", () => {
  const result = catalog({
    scalarFacets: [status],
    filterOptions: [{ id: "declared", label: "Authored open", filter: { status: { exact: "open" } } }],
    contributedFilterOptions: [{ id: "contributed", label: "Contributed open", filter: { status: { exact: "open" } } }],
  });
  expect(result.filters.map((option) => option.id)).toEqual(["declared"]);
  const options = [...result.filters, ...result.facets.flatMap((facet) => facet.options)];
  const predicates = options.map((option) => JSON.stringify(query.toWhere(option.filter)));
  expect(new Set(predicates).size).toBe(options.length);
  expect(result.facets.find((facet) => facet.field === "status")?.options.map((option) => option.id)).toContain("bucket-blank");
});

test("facet declarations override field labels and scalar enum inference remains available", () => {
  const result = catalog({ scalarFacets: [status], customFilterFields: [
    { id: "status", label: "Authored status", type: "selection", options: [{ value: "open", label: "Authored open" }] },
  ] });
  const facet = result.facets.find((item) => item.field === "status")!;
  expect(facet.label).toBe("Authored status");
  expect(facet.options.find((option) => option.value === "open")).toMatchObject({ id: "bucket-open", label: "Authored open" });
  expect(catalog().facets.find((item) => item.field === "status")?.options.map((item) => item.value)).toEqual(["open", "closed"]);
});

test("relations require a declared facet; blank buckets retain their drill and stay out of typed values", () => {
  expect(catalog().facets.some((facet) => facet.field === "owner")).toBe(false);
  const owner: SearchFacet = { field: "owner", label: "Owner", source: "relation", options: [
    { id: "no-owner", label: "Not given", filter: { owner: { isNull: true } } },
    { id: "first-owner", label: "First owner", value: "first", filter: { owner: { exact: "first" } } },
  ] };
  const result = catalog({ declaredFacets: [owner] });
  expect(result.facets.find((facet) => facet.field === "owner")?.options).toEqual(owner.options);
  expect(result.fields.find((field) => field.field === "owner")?.options).toEqual([{ value: "first", label: "First owner" }]);
});

test("text defaults and shortcuts deduplicate and require iContains; null removes only the default", () => {
  const resourceQuery = ResourceQuery.from(testDataResource("notes.Note", { query: query.contract, recordRepresentation: "title" }));
  expect(searchTextFields(resourceQuery)).toEqual(["title"]);
  expect(searchTextFields(query)).toEqual([]);
  expect(searchTextFields(query, "owner.name", ["title", "owner.name", "amount"])).toEqual(["owner.name", "title"]);
  expect(searchTextFields(query, null, ["owner.name", "amount"])).toEqual(["owner.name"]);
  expect(searchTextFields(query, null)).toEqual([]);
  expect(catalog({ textFilterField: "owner.name" }).text[0]?.field).toBe("owner.name");
  const search = { shortcuts: [{ kind: "text", field: "title" }, { kind: "text", field: "owner.name" }] } as const;
  expect(catalog({ textFilterField: "owner.name", search }).text.map((item) => item.field)).toEqual(["owner.name", "title"]);
  expect(catalog({ textFilterField: null, search }).text.map((item) => item.field)).toEqual(["title", "owner.name"]);
});

test.each(["Who wrote it", { label: "Who wrote it" }])("filter-only field and text shortcut labels come from vocabulary: %j", (word) => {
  const resource = testDataResource("test.Record", { query: testResourceQuery({ fields: {
    author_name: testQueryField("author_name", { row: null,
      filter: { field: "author_name", scalar: "String", values: [], operators: ["exact", "iContains"] } }),
  } }) });
  const metadata = schemaFieldMetadataWithVocabulary(schemaFieldMetadataFromDataResources([resource]), {
    "test.Record": { fields: { author_name: word } },
  });
  const modelMetadata = metadata.labels["test.Record"]!;
  expect(modelMetadata.resource).toBe(resource);
  expect(Object.keys(modelMetadata.fields)).toEqual([]);
  const search = { shortcuts: [{ kind: "text", field: "author_name" }] } as const;
  const result = catalog({ query: ResourceQuery.from(resource), modelMetadata,
    columns: [], textFilterField: null, search });
  expect(result.fields.find((field) => field.field === "author_name")?.label).toBe("Who wrote it");
  expect(result.text).toEqual([{ field: "author_name", label: "Who wrote it" }]);
  render(<SearchControls search={searchFixture({ catalog: result })} shortcuts={pageSearchShortcuts(search)} />);
  expect(screen.getByRole("searchbox", { name: "Who wrote it" }).getAttribute("placeholder")).toBe("Who wrote it");
});

test("curated grouping preserves declared, contributed and inferred precedence", () => {
  const authored = { id: "authored", label: "Authored status", group: { field: "status" } };
  const contributed = { id: "contributed", label: "Contributed owner", group: { field: "owner" } };
  expect(catalog({ groupOptions: [authored], contributedGroupOptions: [contributed] }).curatedGroups).toEqual([authored]);
  expect(catalog({ contributedGroupOptions: [contributed] }).curatedGroups).toEqual([contributed]);
  expect(catalog().curatedGroups.length).toBeGreaterThan(0);
  expect(catalog({ groupOptions: [] }).curatedGroups).toEqual([]);
});

test("the box catalog retains every query axis and filterable field regardless of visible columns", () => {
  const full = ResourceQuery.forRows({ fields: {
    title: { scalar: "String" }, status: { kind: "enum", values: [{ value: "open" }] },
    owner: { scalar: "ID" }, amount: { scalar: "Float" }, created: { scalar: "DateTime" },
    metadata: { kind: "json", scalar: "JSON" },
  } }).contract;
  for (const [field, axis] of Object.entries(full.axes)) axis.server = { input: field, key: field };
  const complete = ResourceQuery.from(testDataResource("test.Record", { query: full }));
  const result = catalog({ query: complete, serverGrouping: true, columns: [{ field: "title" }],
    inferOptions: false, groupOptions: [], customFilterFields: [] });
  expect(Object.keys(complete.axes).length).toBeGreaterThan(2);
  expect(result.fields.length).toBeGreaterThan(2);
  expect(result.groups.map((axis) => axis.group.field).sort()).toEqual(Object.keys(complete.axes).sort());
  expect(result.fields.map((field) => field.field ?? field.id).sort()).toEqual(Object.entries(complete.fields)
    .filter(([, field]) => field.filter?.operators.length).map(([name]) => name).sort());
});

test("date grouping offers only granularities whose groups can be opened", () => {
  const contract = ResourceQuery.forRows({ fields: { created: { scalar: "DateTime" } } }).contract;
  contract.axes.created!.server = { input: "created", key: "created" };
  contract.axes.created!.extractions = [
    { name: "year_number", input: "YEAR_NUMBER", key: "number" },
    { name: "month", input: "MONTH", key: "month", rangeKey: "range", drill: {
      kind: "range", field: "created", valueKey: "month", rangeKey: "range", valueMap: [], nullMode: "isNull",
    } },
  ];
  const result = catalog({ query: ResourceQuery.from(testDataResource("test.Record", { query: contract })), serverGrouping: true, columns: [] });
  expect(result.groups[0]?.granularities).toEqual(["month"]);
});

test("production catalog drops invalid declared and contributed predicates and their toggle shortcuts", () => {
  vi.mocked(developmentMode).mockReturnValue(false);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const valid = { id: "open", label: "Open", filter: { status: { exact: "open" } } };
  const search = { shortcuts: [{ kind: "toggle", id: "setup" }, { kind: "toggle", id: "open" }] } as const;
  const result = catalog({
    filterOptions: [{ id: "setup", label: "Accepted", filter: { "stage.name": { exact: "Accepted" } } }, valid],
    contributedFilterOptions: [{ id: "contributed-invalid", label: "Missing", filter: { missing: { exact: "value" } } }],
    scalarFacets: [{ ...status, options: [...status.options,
      { id: "invalid-bucket", label: "Invalid bucket", filter: { missing: { exact: "value" } } },
    ] }],
    search,
  });
  expect(result.filters).toEqual([valid]);
  expect(result.facets.flatMap((facet) => facet.options).some((option) => option.id === "invalid-bucket")).toBe(false);
  expect(error).toHaveBeenCalledTimes(3);
  render(<SearchControls search={searchFixture({ catalog: result })} shortcuts={pageSearchShortcuts(search)} />);
  expect(screen.getByRole("button", { name: "Open" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Accepted" })).toBeNull();
  expect(error).toHaveBeenCalledTimes(4);
  expect(error).toHaveBeenCalledWith('Search shortcut "page.toggle.setup": unknown toggle id "setup".');
});

test("development catalog fails during render with the invalid filter's identity", () => {
  expect(() => catalog({ filterOptions: [
    { id: "setup", label: "Accepted", filter: { "stage.name": { exact: "Accepted" } } },
  ] })).toThrow(/setup.*filter.stage.name.*unknown or non-filterable field/);
});

test("metadata-dependent field shortcuts are omitted in production and retain valid shortcuts", () => {
  vi.mocked(developmentMode).mockReturnValue(false);
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  const search = { shortcuts: [
    { kind: "clause", field: "missing" }, { kind: "text", field: "amount" },
    { kind: "facet", field: "owner" }, { kind: "text", field: "title" },
  ] } as const;
  const result = catalog({ search });
  expect(error).not.toHaveBeenCalled();
  const shortcuts = pageSearchShortcuts(search);
  const rendered = render(<SearchControls search={searchFixture({ catalog: result })} shortcuts={shortcuts} />);
  expect(screen.getAllByRole("searchbox").map((input) => input.getAttribute("aria-label"))).toEqual(["Title"]);
  rendered.rerender(<SearchControls search={searchFixture({ catalog: { ...result } })} shortcuts={[...shortcuts]} />);
  expect(error).toHaveBeenCalledTimes(3);
  expect(error).toHaveBeenCalledWith('Search shortcut "page.clause.missing": field "missing" is not filterable.');
  expect(error).toHaveBeenCalledWith('Search shortcut "page.text.amount": field "amount" does not support iContains.');
  expect(error).toHaveBeenCalledWith('Search shortcut "page.facet.owner": field "owner" needs a facet catalog.');
});
