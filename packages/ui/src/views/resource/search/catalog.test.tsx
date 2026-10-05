// @vitest-environment happy-dom
import type { ReactNode } from "react";
import { cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { testDataResource } from "@angee/metadata/testing";
import { ResourceQuery } from "@angee/metadata";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useSearchCatalog, searchTextFields, type UseSearchCatalogInput } from "./catalog";
import type { SearchFacet } from "./types";

afterEach(cleanup);
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
