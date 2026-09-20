import { ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { expect, test } from "vitest";
import { filterForResourceTextSearch, filterForTextSearch, queryForColumns } from "./resource-query";

const query = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, summary: { scalar: "String" }, status: { scalar: "String" },
} });

test("rows search expands across columns while preserving comparisons on the search field", () => {
  const filter = filterForTextSearch(query, {
    title: { iContains: "alpha", ne: "Forbidden" }, status: { exact: "active" },
  }, "title", ["title", "summary"]);
  expect(query.matches({ title: "Other", summary: "Alpha found", status: "active" }, filter)).toBe(true);
  expect(query.matches({ title: "Forbidden", summary: "Alpha found", status: "active" }, filter)).toBe(false);
  expect(query.matches({ title: "Other", summary: "Alpha found", status: "draft" }, filter)).toBe(false);
});

test("rows search preserves empty membership predicates and rejects unknown fields", () => {
  expect(query.matches({ title: "Alpha", status: "active" }, filterForTextSearch(query,
    { title: { iContains: "alpha" }, status: { inList: [] } }, "title", ["title"]))).toBe(false);
  expect(() => filterForTextSearch(query, { missing: { exact: "x" } })).toThrow(/missing/);
});

test("resource search expands its semantic term across backend-authored fields", () => {
  const searchable = (name: string) => testQueryField(name, {
    filter: {
      field: name,
      scalar: "String",
      values: [],
      operators: ["exact", "iContains"],
    },
  });
  const resource = testDataResource("accounting.Invoice", {
    recordRepresentation: "title",
    recordSearchFields: ["supplier_reference", "title", "number"],
    query: testResourceQuery({ fields: {
      kind: searchable("kind"),
      number: searchable("number"),
      supplier_reference: searchable("supplier_reference"),
      title: testQueryField("title"),
    } }),
  });
  const metadata = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;
  expect(filterForResourceTextSearch(metadata, {
    kind: { exact: "VENDOR_BILL" },
    supplier_reference: { iContains: "S-1MX0XZ7140-2405" },
  })).toEqual({
    kind: { exact: "VENDOR_BILL" },
    OR: [
      { supplier_reference: { iContains: "S-1MX0XZ7140-2405" } },
      { number: { iContains: "S-1MX0XZ7140-2405" } },
    ],
  });
});

test("resource search falls back only to its searchable record representation", () => {
  const resource = testDataResource("notes.Note", {
    recordRepresentation: "title",
    query: testResourceQuery({ fields: {
      hidden: testQueryField("hidden", {
        filter: { field: "hidden", scalar: "String", values: [], operators: ["exact", "iContains"] },
      }),
      title: testQueryField("title", {
        filter: { field: "title", scalar: "String", values: [], operators: ["exact", "iContains"] },
      }),
    } }),
  });
  const metadata = schemaFieldMetadataFromDataResources([resource]).labels[resource.modelLabel]!;

  expect(filterForResourceTextSearch(metadata, {
    title: { iContains: "alpha" },
  })).toEqual({
    title: { iContains: "alpha" },
  });
});

test("bare rows query declares local grouping without enabling server dimensions", () => {
  const rows = queryForColumns([{ field: "title" }], null, [{ field: "created", granularity: "month" }]);
  expect(rows.axis("created", "month").identity({ created: "2026-09-07T12:00:00Z" })).toBe("2026-09");
  expect(() => rows.axis("title").groupBy()).toThrow(/server/);
});
