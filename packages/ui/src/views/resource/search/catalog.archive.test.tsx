// @vitest-environment happy-dom
import type { ReactNode } from "react";
import { cleanup, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ResourceQuery, schemaFieldMetadataFromDataResources, type DataResourceMetadata, type QueryFilter } from "@angee/metadata";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { AppRuntimeProvider, createAngeeI18nInstance } from "../../../runtime";
import { ResourceViewProvider, useResourceView } from "../resource-view-context";
import { useResourceViewQueryFacts } from "../surface/table-state";
import { useSearchCatalog, type UseSearchCatalogInput } from "./catalog";
import { SearchBox } from "./SearchBox";
import { useResourceSearch } from "./use-resource-search";

afterEach(cleanup);

const archiveField = testQueryField("is_archived", {
  scalar: "Boolean",
  filter: { field: "is_archived", scalar: "Boolean", values: [], operators: ["exact"] },
});
const record = testDataResource("test.Record", {
  fields: [{ name: "is_archived", kind: "scalar", scalar: "Boolean", archivable: true,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: { is_archived: archiveField } }),
});

function catalog(resource: DataResourceMetadata, input: Partial<UseSearchCatalogInput<Record<string, unknown>>> = {}) {
  const modelMetadata = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  return renderHook(() => useSearchCatalog({
    columns: [], rows: [], modelMetadata, resourceView: useResourceView(), ...input,
  }), { wrapper: ({ children }: { children: ReactNode }) => <ResourceViewProvider scope="local">{children}</ResourceViewProvider> }).result.current;
}

test("an archivable collection offers Archived without a column, update root or inferred options", () => {
  const result = catalog({ ...record, roots: { list: "records" } }, { inferOptions: false });
  expect(result.filters).toEqual([{
    id: "record.archive.archived", label: "Archived", filter: { is_archived: { exact: true } },
  }]);
});

test("the archive contribution uses the declared flag and query wire alias", () => {
  const resource = { ...record,
    fields: record.fields?.map((field) => ({ ...field, name: "retired" })),
    query: testResourceQuery({ fields: { retired: archiveField } }),
  };
  const result = catalog(resource);
  expect(result.filters[0]?.filter).toEqual({ retired: { exact: true } });
  expect(result.query?.toWhere(result.filters[0]!.filter)).toEqual({ is_archived: { _eq: true } });
});

test.each([false, undefined])("an unclassified archive-named field offers no Archived option (%s)", (archivable) => {
  expect(catalog({ ...record, fields: record.fields?.map((field) => ({ ...field, archivable, trashable: true })) }).filters).toEqual([]);
});

test.each([
  testResourceQuery(),
  testResourceQuery({ fields: { is_archived: { ...archiveField, filter: null } } }),
  testResourceQuery({ fields: { is_archived: { ...archiveField,
    filter: { ...archiveField.filter!, operators: ["inList"] },
  } } }),
])("Archived is absent when the collection query cannot filter the flag exactly: %j", (query) => {
  expect(catalog({ ...record, query }).filters).toEqual([]);
  // The catalog honors the query being used by the collection, including an explicit contract.
  expect(catalog(record, { query: ResourceQuery.from({ ...record, query }) }).filters).toEqual([]);
});

test("a page's equivalent archive predicate keeps its id and label with no duplicate choice", () => {
  const declared = { id: "retired", label: "Retired records", filter: { is_archived: true } };
  const result = catalog(record, { columns: [{ field: "is_archived" }], filterOptions: [declared] });
  expect(result.filters).toEqual([declared]);
  const choices = [...result.filters, ...result.facets.flatMap((facet) => facet.options)];
  expect(choices.filter((option) => JSON.stringify(result.query?.toWhere(option.filter))
    === JSON.stringify({ is_archived: { _eq: true } }))).toEqual([declared]);
});

test("the archive option joins existing contributions and keeps their predicate precedence", () => {
  const contributed = { id: "retired", label: "Retired records", filter: { is_archived: { exact: true } } };
  expect(catalog(record, { contributedFilterOptions: [contributed] }).filters).toEqual([contributed]);
});

test("selecting Archived renders the localized chip and clearing it restores the default collection scope", async () => {
  const modelMetadata = schemaFieldMetadataFromDataResources([record]).labels[record.modelLabel]!;
  const query = ResourceQuery.from(record);
  const i18n = createAngeeI18nInstance({ ui: { "record.archive.archived": "Archived records" } });
  let editable: QueryFilter = {};
  let where: ReturnType<ResourceQuery["toWhere"]> = {};
  function Content() {
    const resourceView = useResourceView();
    const catalog = useSearchCatalog({ columns: [], rows: [], modelMetadata, resourceView });
    const facts = useResourceViewQueryFacts({ columns: [], modelMetadata, resourceView });
    editable = resourceView.state.filter;
    where = query.toWhere(facts.mergedFilter);
    return <SearchBox search={useResourceSearch({ resourceView, catalog })} />;
  }
  render(<AppRuntimeProvider runtime={{ i18n }}>
    <ResourceViewProvider scope="local"><Content /></ResourceViewProvider>
  </AppRuntimeProvider>);

  expect(editable).toEqual({});
  expect(where).toEqual({ is_archived: { _eq: false } });
  const input = screen.getByRole("combobox", { name: "Filter records" });
  fireEvent.input(input, { target: { value: "Archived" }, inputType: "insertText" });
  const option = await screen.findByRole("option", { name: "Archived records" });
  fireEvent.pointerDown(option, { pointerType: "mouse" });
  fireEvent.click(option);

  const chip = await screen.findByRole("button", { name: "Remove Archived records" });
  expect(within(screen.getByRole("toolbar", { name: "Active search" })).getAllByRole("button")).toEqual([chip]);
  expect(editable).toEqual({ is_archived: { exact: true } });
  expect(where).toEqual({ is_archived: { _eq: true } });

  fireEvent.click(chip);
  await waitFor(() => expect(screen.queryByRole("button", { name: "Remove Archived records" })).toBeNull());
  expect(editable).toEqual({});
  expect(where).toEqual({ is_archived: { _eq: false } });
});
