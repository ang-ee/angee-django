// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import type { ResourceFacetOption } from "@angee/refine";
import { ResourceQuery, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { scalarFacetDeclarations, useScalarFacets } from "./scalar-facet";

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
    expect(result.current.filters).toEqual([
      {
        id: "status:DRAFT",
        label: "Draft",
        chipLabel: "Draft",
        filter: { status: { exact: "DRAFT" } },
      },
      {
        id: "status:ACTIVE",
        label: "Active",
        chipLabel: "Active",
        filter: { status: { exact: "ACTIVE" } },
      },
      {
        // A free-text scalar value renders verbatim — only enum-typed fields
        // get their member names prettified.
        id: "source:api",
        label: "api",
        chipLabel: "api",
        filter: { source: { exact: "api" } },
      },
    ]);
    expect(result.current.filterFields).toEqual([
      {
        id: "status",
        field: "status",
        label: "Status",
        type: "selection",
        options: [
          { value: "DRAFT", label: "Draft" },
          { value: "ACTIVE", label: "Active" },
        ],
      },
      {
        id: "source",
        field: "source",
        label: "Source",
        type: "selection",
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
