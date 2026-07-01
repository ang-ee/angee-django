// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { useAuthoredMutation } from "./authored-hooks";

const mutationMock = vi.hoisted(() => ({
  calls: [] as Array<{
    dataProviderName: string;
    generation: number;
    values: Record<string, unknown>;
  }>,
  generation: 0,
}));

const invalidationMock = vi.hoisted(() => ({
  authoredPredicateModels: [] as readonly string[],
  queryInvalidations: 0,
  resourceLabels: [] as readonly string[],
}));

vi.mock("@refinedev/core", () => ({
  useCustom: vi.fn(),
  useCustomMutation: () => {
    mutationMock.generation += 1;
    const generation = mutationMock.generation;
    return {
      mutateAsync: vi.fn(
        async (payload: {
          dataProviderName: string;
          values: Record<string, unknown>;
        }) => {
          mutationMock.calls.push({
            dataProviderName: payload.dataProviderName,
            generation,
            values: payload.values,
          });
          return {
            data: {
              data: { generation, variables: payload.values },
            },
          };
        },
      ),
      mutation: { isPending: false, error: null },
    };
  },
  useInvalidate: () => vi.fn(async () => undefined),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({
    invalidateQueries: vi.fn(
      async (options: { predicate?: (query: { meta: unknown }) => boolean }) => {
        invalidationMock.queryInvalidations += 1;
        options.predicate?.({ meta: { angeeModels: ["messaging.ThreadFollower"] } });
      },
    ),
  }),
}));

vi.mock("@angee/resources", () => ({
  modelMetadataForLabel: (_metadata: unknown, modelLabel: string) => {
    if (modelLabel === "notes.Note") {
      return {
        resource: {
          modelLabel: "notes.Note",
          roots: { list: "notes" },
          schemaName: "console",
        },
      };
    }
    if (modelLabel === "messaging.ThreadActivity") {
      return {
        resource: {
          modelLabel: "messaging.ThreadActivity",
          roots: { changes: "threadActivityChanged" },
          schemaName: "console",
        },
      };
    }
    return null;
  },
  refineInvalidationParams: (target: unknown) => target,
  resourceInvalidationTargets: (_metadata: unknown, modelLabels: readonly string[]) => {
    if (modelLabels.includes("messaging.ThreadActivity")) {
      throw new Error("changes-only resources cannot be invalidated through refine");
    }
    invalidationMock.resourceLabels = modelLabels;
    return [];
  },
  useActiveGraphQLSchemaName: () => "console",
  useSchemaFieldMetadata: () => ({}),
}));

vi.mock("@angee/refine", () => ({
  authoredQueryMeta: (models: readonly string[]) => ({ models }),
  authoredQueryReadsAnyModel: (_meta: unknown, models: readonly string[]) => {
    invalidationMock.authoredPredicateModels = models;
    return false;
  },
  useStableArray: <T,>(value: readonly T[]) => value,
  useStableVariables: <T,>(value: T) => value,
}));

beforeEach(() => {
  mutationMock.calls = [];
  mutationMock.generation = 0;
  invalidationMock.authoredPredicateModels = [];
  invalidationMock.queryInvalidations = 0;
  invalidationMock.resourceLabels = [];
});

describe("useAuthoredMutation", () => {
  test("keeps mutate identity stable while calling the latest refine mutation", async () => {
    const document = "mutation Probe { probe }" as never;
    const { result, rerender } = renderHook(() => useAuthoredMutation(document));
    const firstMutate = result.current[0];

    rerender();

    expect(result.current[0]).toBe(firstMutate);

    let data: unknown;
    await act(async () => {
      data = await result.current[0]({ value: "fresh" } as never);
    });

    expect(data).toEqual({ generation: 2, variables: { value: "fresh" } });
    expect(mutationMock.calls).toEqual([
      {
        dataProviderName: "console",
        generation: 2,
        values: { value: "fresh" },
      },
    ]);
  });

  test("keeps internal and changes-only labels for authored query invalidation only", async () => {
    const document = "mutation Probe { probe }" as never;
    const { result } = renderHook(() =>
      useAuthoredMutation(document, {
        invalidateModels: [
          "notes.Note",
          "messaging.ThreadFollower",
          "messaging.ThreadActivity",
        ],
      }),
    );

    await act(async () => {
      await result.current[0]({ value: "fresh" } as never);
    });

    expect(invalidationMock.resourceLabels).toEqual(["notes.Note"]);
    expect(invalidationMock.queryInvalidations).toBe(1);
    expect(invalidationMock.authoredPredicateModels).toEqual([
      "notes.Note",
      "messaging.ThreadFollower",
      "messaging.ThreadActivity",
    ]);
  });
});
