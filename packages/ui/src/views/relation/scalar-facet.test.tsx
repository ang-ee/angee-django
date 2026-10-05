// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import type { ResourceFacetOption } from "@angee/refine";
import { ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { extractFacet, groupDimension } from "@angee/refine";
import { testDataResource } from "@angee/metadata/testing";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { scalarFacetDeclarations, useScalarFacets } from "./scalar-facet";
import { createResourceViewState } from "../resource/resource-view-model";
import { activeItems } from "../resource/search/active";
import { searchFixture } from "../resource/search/search-fixture.test-support";

const dataMocks = vi.hoisted(() => {
  const groupsDocument = { kind: "groups-document" };
  return {
    facets: vi.fn(),
    groupsDocument,
    operationDocuments: {
      public: {
        groups: { "notes.Note": groupsDocument },
      },
    },
  };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
  return {
    ...actual,
    useAngeeFacets: dataMocks.facets,
    useOperationDocuments: () => dataMocks.operationDocuments,
  };
});

const GROUPS_TARGET = { dataProviderName: "public", root: "notes_groups", modelLabel: "notes.Note" };

beforeEach(() => {
  dataMocks.facets.mockReset();
  dataMocks.facets.mockReturnValue(resourceFacets({
    status: [
      {
        value: "DRAFT",
        label: "DRAFT",
        count: 2,
        key: { status: "DRAFT" },
      },
      {
        value: "ACTIVE",
        label: "ACTIVE",
        count: 1,
        key: { status: "ACTIVE" },
      },
    ],
    source: [
      {
        value: "api",
        label: "api",
        count: 1,
        key: { source: "api" },
      },
    ],
  }));
});

describe("useScalarFacets", () => {
  test("queries categorical scalar facets from resource metadata", () => {
    const { result } = renderHook(() =>
      useScalarFacets(
        "notes.Note",
        [
          { field: "title" },
          { field: "status", widget: "statusBadge" },
          { field: "source", widget: "statusBadge" },
          { field: "wordCount" },
          { field: "updatedAt" },
        ],
        NOTE_METADATA,
        { title: { iContains: "release" }, status: { exact: "DRAFT" } },
      ));

    expect(dataMocks.facets).toHaveBeenCalledWith(GROUPS_TARGET, {
      document: dataMocks.groupsDocument,
      facets: [
        {
          id: "status",
          dimensions: [{ input: "STATUS", key: "status" }],
          orderBy: [{ field: "status", direction: "ASC", nulls: "LAST" }],
          valueKey: "status",
          pageSize: 200,
          where: { title: { _ilike: "%release%" } },
        },
        {
          id: "source",
          dimensions: [{ input: "SOURCE", key: "source" }],
          orderBy: [{ field: "source", direction: "ASC", nulls: "LAST" }],
          valueKey: "source",
          pageSize: 200,
          where: {
            _and: [
              { status: { _eq: "DRAFT" } },
              { title: { _ilike: "%release%" } },
            ],
          },
        },
      ],
      enabled: true,
    });
    expect(result.current.flatMap((facet) => facet.options)).toEqual([
      {
        id: 'status:"DRAFT"',
        label: "Draft",
        filter: { status: { exact: "DRAFT" } }, value: "DRAFT",
      },
      {
        id: 'status:"ACTIVE"',
        label: "Active",
        filter: { status: { exact: "ACTIVE" } }, value: "ACTIVE",
      },
      {
        // A free-text scalar value renders verbatim — only enum-typed fields
        // get their member names prettified.
        id: 'source:"api"',
        label: "api",
        filter: { source: { exact: "api" } }, value: "api",
      },
    ]);
    expect(result.current).toMatchObject([
      {
        field: "status",
        label: "Status",
        source: "scalar",
        options: [
          { value: "DRAFT", label: "Draft" },
          { value: "ACTIVE", label: "Active" },
        ],
      },
      {
        field: "source",
        label: "Source",
        source: "scalar",
        options: [{ value: "api", label: "api" }],
      },
    ]);
  });

  test("uses the canonical scalar axis without a display alias", () => {
    expect(scalarFacetDeclarations([], INTEGRATION_METADATA)).toEqual([
      {
        id: "implClass",
        field: "implClass",
        label: "Impl Class",
        group: {
          field: "implClass",
        },
        spec: {
          id: "implClass",
          dimensions: [{ input: "IMPL_CLASS", key: "implClass" }],
          orderBy: [{ field: "implClass", direction: "ASC", nulls: "LAST" }],
          valueKey: "implClass",
          pageSize: 200,
          where: {},
        },
      },
    ]);
  });

  test.each(["choice", "text"])("shows distinct blank and null %s facets and active chips with exact drill filters", (kind) => {
    const contract = ResourceQuery.forRows({ fields: {
      tax_country: kind === "choice"
        ? { kind: "enum", values: [{ value: "" }, { value: "DE", description: "Germany" }] }
        : { scalar: "String" },
    } }).contract;
    const blankKey = kind === "choice" ? "BLANK" : "";
    const valueMap = kind === "choice" ? [{ from: blankKey, to: "" }] : [];
    contract.fields.tax_country!.filter = { field: "tax_country", scalar: "String", values: [], operators: ["exact", "isNull"] };
    contract.axes.tax_country!.server = { input: "TAX_COUNTRY", key: "tax_country", valueMap };
    contract.axes.tax_country!.drill = { kind: "value", field: "tax_country", valueKey: "tax_country", nullMode: "isNull",
      valueMap };
    const metadata = schemaFieldMetadataFromDataResources([testDataResource("parties.Party", {
      query: contract,
      fields: [{ name: "tax_country", kind: kind === "choice" ? "enum" : "scalar", scalar: "String", values: contract.fields.tax_country!.values,
        readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
    })]).labels["parties.Party"]!;
    const facet = extractFacet({ parties_groups: [
      { key: { tax_country: blankKey }, aggregate: { count: 2 } },
      { key: { tax_country: null }, aggregate: { count: 1 } },
      { key: { tax_country: "DE" }, aggregate: { count: 3 } },
    ], totalCount: 3 }, "parties_groups", { id: "tax_country", dimensions: [groupDimension("TAX_COUNTRY", "tax_country")] });
    dataMocks.facets.mockReturnValue(resourceFacets({ tax_country: facet.options }));
    const { result } = renderHook(() => useScalarFacets("parties.Party", [{ field: "tax_country", widget: "statusBadge" }], metadata));
    expect(result.current.flatMap((facet) => facet.options)).toEqual([
      { id: 'tax_country:""', label: "Blank", filter: { tax_country: { exact: "" } }, value: "" },
      { id: "tax_country:null", label: "No value", filter: { tax_country: { isNull: true } } },
      { id: 'tax_country:"DE"', label: kind === "choice" ? "Germany" : "DE", filter: { tax_country: { exact: "DE" } }, value: "DE" },
    ]);
    expect(result.current.flatMap((facet) => facet.options).map((option) => ResourceQuery.from(metadata).toWhere(option.filter))).toEqual([
      { tax_country: { _eq: "" } }, { tax_country: { _is_null: true } }, { tax_country: { _eq: "DE" } },
    ]);
    expect(result.current[0]?.options[1]?.value).toBeUndefined();
    expect(result.current[0]?.source).toBe("scalar");
    const catalog = searchFixture({ catalog: { facets: result.current } }).catalog;
    for (const option of result.current[0]!.options.slice(0, 2)) {
      expect(activeItems(createResourceViewState({ filter: option.filter }), catalog)).toEqual([
        { id: "facet:tax_country", kind: "facet", field: "tax_country", label: "Tax Country", options: [option] },
      ]);
    }
  });
});

const noteQuery = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, status: { kind: "enum", values: [{ value: "DRAFT", description: "Draft" }, { value: "ACTIVE", description: "Active" }] },
  source: { scalar: "String" }, wordCount: { scalar: "Int" }, updatedAt: { scalar: "DateTime" },
} }).contract;
for (const field of ["status", "source", "wordCount", "updatedAt"]) {
  noteQuery.axes[field]!.server = { input: field === "wordCount" ? "WORD_COUNT" : field === "updatedAt" ? "UPDATED_AT" : field.toUpperCase(), key: field };
  noteQuery.axes[field]!.drill = { kind: "value", field, valueKey: field, nullMode: "isNull", valueMap: [] };
}
const NOTE_METADATA = schemaFieldMetadataFromDataResources([testDataResource("notes.Note", {
  schemaName: "public", roots: { groups: "notes_groups" }, query: noteQuery,
  fields: Object.entries(noteQuery.fields).map(([name, field]) => ({ name,
    kind: field.kind === "enum" ? "enum" : "scalar", scalar: field.scalar, values: field.values,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  })),
})]).labels["notes.Note"]!;
const integrationQuery = ResourceQuery.forRows({ fields: {
  implCategory: { scalar: "String" }, implClass: { kind: "enum", values: [{ value: "NONE", description: "None" }] },
} }).contract;
integrationQuery.axes.implClass!.server = { input: "IMPL_CLASS", key: "implClass" };
integrationQuery.axes.implClass!.drill = { kind: "value", field: "implClass", valueKey: "implClass", nullMode: "isNull", valueMap: [] };
const INTEGRATION_METADATA = schemaFieldMetadataFromDataResources([testDataResource("integrate.Integration", {
  query: integrationQuery,
  fields: [{ name: "implClass", kind: "enum", values: integrationQuery.fields.implClass!.values,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
})]).labels["integrate.Integration"]!;

function resourceFacets(
  facets: Record<string, readonly ResourceFacetOption[]>,
) {
  return {
    facets: Object.fromEntries(
      Object.entries(facets).map(([id, options]) => [
        id,
        {
          count: options.reduce((total, option) => total + option.count, 0),
          totalCount: options.length,
          options,
        },
      ]),
    ),
    fetching: false,
    error: null,
    refetch: vi.fn(),
  };
}
