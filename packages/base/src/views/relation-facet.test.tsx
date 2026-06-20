// @vitest-environment happy-dom

import { renderHook } from "@testing-library/react";
import {
  ModelMetadataProvider,
  type SchemaFieldMetadata,
  type UseResourceListResult,
} from "@angee/sdk";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useRelationFacet } from "./relation-facet";

const sdkMocks = vi.hoisted(() => ({
  list: vi.fn(),
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useResourceList: sdkMocks.list,
  };
});

beforeEach(() => {
  sdkMocks.list.mockReturnValue(resourceList([
    { id: "provider-openai", name: "OpenAI" },
    { id: "provider-anthropic", name: "Anthropic" },
  ]));
});

describe("useRelationFacet", () => {
  test("builds filters and a group option from relation metadata", () => {
    const { result } = renderHook(
      () =>
        useRelationFacet("agents.InferenceModel", {
          field: "provider",
          label: "Provider",
        }),
      { wrapper: Metadata },
    );

    expect(sdkMocks.list).toHaveBeenCalledWith("InferenceProvider", {
      fields: ["name"],
      pageSize: 200,
      enabled: true,
    });
    expect(result.current.filters).toEqual([
      {
        id: "provider:provider-anthropic",
        label: "Anthropic",
        chipLabel: "Anthropic",
        filter: { provider: { sqid: "provider-anthropic" } },
      },
      {
        id: "provider:provider-openai",
        label: "OpenAI",
        chipLabel: "OpenAI",
        filter: { provider: { sqid: "provider-openai" } },
      },
    ]);
    expect(result.current.filterFields).toEqual([]);
    expect(result.current.groupOption).toEqual({
      id: "provider.name",
      label: "Provider",
      group: {
        field: "provider.name",
        aggregateField: "provider",
        aggregateKey: "providerId",
      },
    });
  });

  test("keeps filter input and aggregate bucket keys separate", () => {
    const { result } = renderHook(
      () =>
        useRelationFacet("agents.InferenceModel", {
          field: "provider",
          filterField: "provider",
          aggregateKey: "providerId",
        }),
      { wrapper: Metadata },
    );

    expect(result.current.filters[0]).toMatchObject({
      id: "provider:provider-anthropic",
      filter: { provider: { sqid: "provider-anthropic" } },
    });
    expect(result.current.groupOption).toMatchObject({
      id: "provider.name",
      group: {
        field: "provider.name",
        aggregateField: "provider",
        aggregateKey: "providerId",
      },
    });
  });

  test("builds relation preset filters without exposing custom filter fields", () => {
    const { result } = renderHook(
      () => useRelationFacet("agents.InferenceModel", { field: "publisher" }),
      { wrapper: Metadata },
    );

    expect(result.current.filters[0]).toMatchObject({
      id: "publisher:provider-anthropic",
      filter: { publisher: { sqid: "provider-anthropic" } },
    });
    expect(result.current.filterFields).toEqual([]);
    expect(result.current.groupOption).toMatchObject({
      id: "publisher.name",
      group: {
        field: "publisher.name",
        aggregateField: "publisher",
        aggregateKey: "publisher",
      },
    });
  });

  test("suppresses default group options without aggregate-key metadata", () => {
    const { result } = renderHook(
      () => useRelationFacet("agents.InferenceModel", { field: "owner" }),
      { wrapper: Metadata },
    );

    expect(result.current.filters[0]).toMatchObject({
      id: "ownerId:provider-anthropic",
      filter: { ownerId: { exact: "provider-anthropic" } },
    });
    expect(result.current.groupOption).toBeUndefined();
  });

  test("falls back to ids for empty related labels", () => {
    sdkMocks.list.mockReturnValue(resourceList([
      { id: "provider-unnamed", name: "" },
    ]));

    const { result } = renderHook(
      () =>
        useRelationFacet("agents.InferenceModel", {
          field: "provider",
          filterField: "providerId",
        }),
      { wrapper: Metadata },
    );

    expect(result.current.filterFields[0]?.options).toEqual([
      { value: "provider-unnamed", label: "provider-unnamed" },
    ]);
  });

  test("stays inert when the field is not a listable relation", () => {
    const { result } = renderHook(
      () =>
        useRelationFacet("agents.InferenceModel", {
          field: "name",
          filterField: "name",
        }),
      { wrapper: Metadata },
    );

    expect(sdkMocks.list).toHaveBeenLastCalledWith("", {
      fields: ["id"],
      pageSize: 200,
      enabled: false,
    });
    expect(result.current.filters).toEqual([]);
    expect(result.current.filterFields).toEqual([]);
    expect(result.current.groupOption).toBeUndefined();
  });
});

const METADATA: SchemaFieldMetadata = {
  types: {
    InferenceModelType: {
      typeName: "InferenceModelType",
      fields: {
        provider: {
          name: "provider",
          kind: "relation",
          relationTarget: "InferenceProviderType",
          relationFilter: {
            field: "provider",
            mode: "lookup",
            lookup: "sqid",
            aggregateKey: "providerId",
          },
        },
        publisher: {
          name: "publisher",
          kind: "relation",
          relationTarget: "InferenceProviderType",
          relationFilter: {
            field: "publisher",
            mode: "lookup",
            lookup: "sqid",
            aggregateKey: "publisher",
          },
        },
        owner: {
          name: "owner",
          kind: "relation",
          relationTarget: "InferenceProviderType",
          relationFilter: {
            field: "ownerId",
            mode: "lookup",
            lookup: "exact",
          },
        },
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
    },
    InferenceProviderType: {
      typeName: "InferenceProviderType",
      recordRepresentation: "name",
      rootFields: { list: "inferenceProviders" },
      fields: {
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
    },
  },
};

function Metadata({ children }: { children: ReactNode }): ReactNode {
  return (
    <ModelMetadataProvider metadata={METADATA}>
      {children}
    </ModelMetadataProvider>
  );
}

function resourceList(rows: readonly Record<string, unknown>[]): UseResourceListResult {
  return {
    rows,
    total: rows.length,
    pageCount: 1,
    page: 1,
    pageSize: 200,
    pageInfo: undefined,
    hasNext: false,
    hasPrev: false,
    setPage: vi.fn(),
    firstPage: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    lastPage: vi.fn(),
    fetching: false,
    error: null,
    refetch: vi.fn(),
  };
}
