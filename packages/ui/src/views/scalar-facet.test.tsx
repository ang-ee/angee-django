// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import type { ResourceFacetOption } from "@angee/refine";
import type {
  ModelMetadata,
} from "@angee/metadata";
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

const GROUPS_TARGET = { dataProviderName: "public", root: "notes_groups" };

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
            title: { _ilike: "%release%" },
            status: { _eq: "DRAFT" },
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
        id: "source:api",
        label: "Api",
        chipLabel: "Api",
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
        options: [{ value: "api", label: "Api" }],
      },
    ]);
  });

  test("reuses scalar group aliases for bucket queries and labels", () => {
    expect(scalarFacetDeclarations([], INTEGRATION_METADATA)).toEqual([
      {
        id: "implClass",
        field: "implClass",
        label: "Implementation",
        group: {
          field: "implCategory",
          aggregateField: "implClass",
          aggregateKey: "implClass",
        },
        spec: {
          id: "implClass",
          dimensions: [{ input: "IMPL_CLASS", key: "implClass" }],
          orderBy: [{ field: "implClass", direction: "ASC", nulls: "LAST" }],
          valueKey: "implClass",
          pageSize: 200,
        },
        neutralizeFilterFields: ["implClass"],
      },
    ]);
  });
});

const NOTE_METADATA: ModelMetadata = {
  typeName: "NoteType",
  fields: {
    title: { name: "title", kind: "scalar", scalar: "String", label: "Title" },
    status: {
      name: "status",
      kind: "enum",
      enumName: "NoteStatus",
      label: "Status",
      values: [
        { value: "DRAFT", description: "Draft" },
        { value: "ACTIVE", description: "Active" },
      ],
    },
    source: { name: "source", kind: "scalar", scalar: "String", label: "Source" },
    wordCount: { name: "wordCount", kind: "scalar", scalar: "Int" },
    updatedAt: { name: "updatedAt", kind: "scalar", scalar: "DateTime" },
  },
  resource: {
    schemaName: "public",
    modelLabel: "notes.Note",
    appLabel: "notes",
    modelName: "note",
    publicIdField: "sqid",
    roots: { groups: "notes_groups" },
    typeNames: { node: "NoteType" },
    capabilities: ["list", "filter", "groups"],
    filterFields: ["title", "status", "source", "wordCount", "updatedAt"],
    orderFields: [],
    aggregateFields: ["id", "wordCount"],
    groupByFields: ["status", "source", "wordCount", "updatedAt"],
    groupDimensions: [
      {
        field: "status",
        input: "STATUS",
        key: "status",
        kind: "column",
      },
      {
        field: "source",
        input: "SOURCE",
        key: "source",
        kind: "column",
        scalar: "String",
      },
      {
        field: "wordCount",
        input: "WORD_COUNT",
        key: "wordCount",
        kind: "column",
        scalar: "Int",
      },
      {
        field: "updatedAt",
        input: "UPDATED_AT",
        key: "updatedAt",
        kind: "column",
        scalar: "DateTime",
      },
    ],
    relationAxes: [],
  },
};

const INTEGRATION_METADATA: ModelMetadata = {
  typeName: "IntegrationType",
  fields: {
    implCategory: {
      name: "implCategory",
      kind: "scalar",
      scalar: "String",
      label: "Implementation",
    },
    implClass: {
      name: "implClass",
      kind: "enum",
      enumName: "IntegrationImplsImpl",
      values: [{ value: "NONE", description: "None" }],
    },
  },
  resource: {
    schemaName: "console",
    modelLabel: "integrate.Integration",
    appLabel: "integrate",
    modelName: "integration",
    publicIdField: "sqid",
    roots: {},
    typeNames: { node: "IntegrationType" },
    capabilities: ["list", "filter", "groups"],
    filterFields: ["implClass"],
    orderFields: [],
    aggregateFields: ["id"],
    groupByFields: ["implClass"],
    groupDimensions: [
      {
        field: "implClass",
        input: "IMPL_CLASS",
        key: "implClass",
        kind: "column",
      },
    ],
    relationAxes: [],
    groupAliases: [
      {
        field: "implCategory",
        aggregateField: "implClass",
        aggregateKey: "implClass",
      },
    ],
  },
};

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
