// @vitest-environment happy-dom

import {
  renderHook } from "@testing-library/react";
import type { ResourceFacetOption } from "@angee/refine";
import {
  ResourceQuery,
  schemaFieldMetadataFromDataResources,
  ModelMetadataProvider,
} from "@angee/metadata";
import {
  testDataResource,
} from "@angee/metadata/testing";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useRelationFacets } from "./relation-facet";

const dataMocks = vi.hoisted(() => {
  const groupsDocument = { kind: "groups-document" };
  return {
    facets: vi.fn(),
    groupsDocument,
    operationDocuments: {
      console: {
        groups: { "agents.InferenceModel": groupsDocument },
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

const GROUPS_TARGET = {
  modelLabel: "agents.InferenceModel",
  dataProviderName: "console",
  root: "inference_models_groups",
};

beforeEach(() => {
  dataMocks.facets.mockReset();
  dataMocks.facets.mockReturnValue(resourceFacets({
    provider: facetOptions(),
    publisher: facetOptions().map((option) => ({ ...option, key: { publisher: option.value } })),
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
      GROUPS_TARGET,
      {
        document: dataMocks.groupsDocument,
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
          }, {
            field: "providerId",
            direction: "ASC",
            nulls: "LAST",
          }],
          valueKey: "providerId",
          labelKey: "provider_Name",
          pageSize: 200,
          where: {},
        }],
        enabled: true,
      },
    );
    expect(result.current.filters).toEqual([
      {
        id: "provider:provider-anthropic",
        label: "Anthropic",
        chipLabel: "Anthropic",
        filter: { provider: { exact: "provider-anthropic" } },
      },
      {
        id: "provider:provider-openai",
        label: "OpenAI",
        chipLabel: "OpenAI",
        filter: { provider: { exact: "provider-openai" } },
      },
    ]);
    expect(result.current.filterFields).toEqual(expect.arrayContaining([expect.objectContaining({ type: "selection" })]));
    expect(result.current.groupOptions).toEqual([{
      id: "provider",
      label: "Provider",
      group: {
        field: "provider",
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
            provider: { exact: "provider-openai" },
            name: { iContains: "launch" },
          },
        ),
      { wrapper: Metadata },
    );

    expect(dataMocks.facets).toHaveBeenCalledWith(
      GROUPS_TARGET,
      {
        document: dataMocks.groupsDocument,
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
          }, {
            field: "providerId",
            direction: "ASC",
            nulls: "LAST",
          }],
          valueKey: "providerId",
          labelKey: "provider_Name",
          pageSize: 200,
          where: { name: { _ilike: "%launch%" } },
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
      filter: { publisher: { exact: "provider-anthropic" } },
    });
    expect(result.current.filterFields).toEqual(expect.arrayContaining([expect.objectContaining({ type: "selection" })]));
    expect(result.current.groupOptions).toEqual([{
      id: "publisher",
      label: "Publisher",
      group: {
        field: "publisher",
      },
    }]);
  });

  test("stays inert when the field is not a listable relation", () => {
    const { result } = renderHook(
      () =>
        useRelationFacets("agents.InferenceModel", [
          { field: "name" },
        ]),
      { wrapper: Metadata },
    );

    expect(dataMocks.facets).toHaveBeenLastCalledWith(
      GROUPS_TARGET,
      {
        document: dataMocks.groupsDocument,
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

const query = ResourceQuery.forRows({ fields: {
  id: { scalar: "ID" }, name: { scalar: "String" },
  provider: { kind: "relation", identityPath: "provider.id", labelPath: "provider.name" },
  publisher: { kind: "relation", identityPath: "publisher.id", labelPath: "publisher.name" },
} }).contract;
for (const field of ["provider", "publisher"]) {
  const key = field === "provider" ? "providerId" : "publisher";
  query.axes[field]!.server = { input: field.toUpperCase(), key,
    ...(field === "provider" ? { labelInput: "PROVIDER__NAME", labelKey: "provider_Name" } : {}),
  };
  query.axes[field]!.drill = { kind: "identity", field, valueKey: key, nullMode: "isNull", valueMap: [] };
}
const METADATA = schemaFieldMetadataFromDataResources([testDataResource("agents.InferenceModel", {
  roots: { groups: "inference_models_groups" }, query,
  fields: ["name", "provider", "publisher"].map((name) => ({ name,
    kind: name === "name" ? "scalar" : "relation", scalar: name === "name" ? "String" : "ID",
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  })),
})]);

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
