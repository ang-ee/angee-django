// @vitest-environment happy-dom

import {
  renderHook } from "@testing-library/react";
import type { ResourceFacetOption } from "@angee/refine";
import {
  ModelMetadataProvider,
} from "@angee/resources";
import type {
  SchemaFieldMetadata,
} from "@angee/resources";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useRelationFacets } from "./relation-facet";

const dataMocks = vi.hoisted(() => ({
  facets: vi.fn(),
}));

vi.mock("../data/hooks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../data/hooks")>();
  return {
    ...actual,
    useAngeeFacets: dataMocks.facets,
  };
});

beforeEach(() => {
  dataMocks.facets.mockReset();
  dataMocks.facets.mockReturnValue(resourceFacets({
    provider: facetOptions(),
    publisher: facetOptions(),
  }));
});

describe("useRelationFacets", () => {
  test("builds declared list facets in one model query", () => {
    const { result } = renderHook(
      () =>
        useRelationFacets("agents.InferenceModel", [
          { field: "provider", label: "Provider" },
        ]),
      { wrapper: Metadata },
    );

    expect(dataMocks.facets).toHaveBeenCalledWith(
      INFERENCE_MODEL_RESOURCE,
      {
        facets: [{
          id: "provider",
          dimensions: [
            { input: "PROVIDER", key: "providerId" },
            { input: "PROVIDER__NAME", key: "provider_Name" },
          ],
          orderBy: [{
            field: "provider_Name",
            direction: "ASC",
            nulls: "LAST",
          }],
          valueKey: "providerId",
          labelKey: "provider_Name",
          pageSize: 200,
        }],
        enabled: true,
      },
    );
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
    expect(result.current.groupOptions).toEqual([{
      id: "provider.name",
      label: "Provider",
      group: {
        field: "provider.name",
        aggregateField: "provider",
        aggregateKey: "providerId",
      },
    }]);
  });

  test("passes active filters to declared facets for neutralized counts", () => {
    renderHook(
      () =>
        useRelationFacets(
          "agents.InferenceModel",
          [{ field: "provider", label: "Provider" }],
          {
            provider: { sqid: "provider-openai" },
            name: { iContains: "launch" },
          },
        ),
      { wrapper: Metadata },
    );

    expect(dataMocks.facets).toHaveBeenCalledWith(
      INFERENCE_MODEL_RESOURCE,
      {
        facets: [{
          id: "provider",
          dimensions: [
            { input: "PROVIDER", key: "providerId" },
            { input: "PROVIDER__NAME", key: "provider_Name" },
          ],
          orderBy: [{
            field: "provider_Name",
            direction: "ASC",
            nulls: "LAST",
          }],
          valueKey: "providerId",
          labelKey: "provider_Name",
          pageSize: 200,
          where: { name: { _ilike: "launch" } },
        }],
        enabled: true,
      },
    );
  });

  test("builds relation preset filters without exposing custom filter fields", () => {
    const { result } = renderHook(
      () =>
        useRelationFacets("agents.InferenceModel", [
          { field: "publisher" },
        ]),
      { wrapper: Metadata },
    );

    expect(result.current.filters[0]).toMatchObject({
      id: "publisher:provider-anthropic",
      filter: { publisher: { sqid: "provider-anthropic" } },
    });
    expect(result.current.filterFields).toEqual([]);
    expect(result.current.groupOptions).toEqual([{
      id: "publisher.name",
      label: "Publisher",
      group: {
        field: "publisher.name",
        aggregateField: "publisher",
        aggregateKey: "publisher",
      },
    }]);
  });

  test("stays inert when the field is not a listable relation", () => {
    const { result } = renderHook(
      () =>
        useRelationFacets("agents.InferenceModel", [
          { field: "name", filterField: "name" },
        ]),
      { wrapper: Metadata },
    );

    expect(dataMocks.facets).toHaveBeenLastCalledWith(
      INFERENCE_MODEL_RESOURCE,
      {
        facets: [],
        enabled: false,
      },
    );
    expect(result.current).toEqual({
      filters: [],
      filterFields: [],
      groupOptions: [],
    });
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
            labelKey: "provider_Name",
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
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
      resource: {
        schemaName: "console",
        modelLabel: "agents.InferenceModel",
        appLabel: "agents",
        modelName: "inferencemodel",
        publicIdField: "sqid",
        roots: {},
        typeNames: { node: "InferenceModelType" },
        capabilities: ["list", "groups"],
        filterFields: ["provider", "publisher"],
        orderFields: [],
        aggregateFields: ["id"],
        groupByFields: ["provider", "provider_Name", "publisher"],
        groupDimensions: [
          {
            field: "provider",
            input: "PROVIDER",
            key: "providerId",
            kind: "relation",
            scalar: "ID",
          },
          {
            field: "provider_Name",
            input: "PROVIDER__NAME",
            key: "provider_Name",
            kind: "column",
            scalar: "String",
          },
          {
            field: "publisher",
            input: "PUBLISHER",
            key: "publisher",
            kind: "relation",
            scalar: "ID",
          },
        ],
        relationAxes: [],
      },
    },
    InferenceProviderType: {
      typeName: "InferenceProviderType",
      recordRepresentation: "name",
      rootFields: { list: "inference_providers" },
      fields: {
        name: { name: "name", kind: "scalar", scalar: "String" },
      },
    },
  },
};

const INFERENCE_MODEL_RESOURCE = METADATA.types.InferenceModelType!.resource!;

function Metadata({ children }: { children: ReactNode }): ReactNode {
  return (
    <ModelMetadataProvider metadata={METADATA}>
      {children}
    </ModelMetadataProvider>
  );
}

function facetOptions(): readonly ResourceFacetOption[] {
  return [
    {
      value: "provider-anthropic",
      label: "Anthropic",
      count: 1,
      key: { providerId: "provider-anthropic" },
    },
    {
      value: "provider-openai",
      label: "OpenAI",
      count: 1,
      key: { providerId: "provider-openai" },
    },
  ];
}

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
