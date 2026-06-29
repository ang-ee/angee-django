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
    invalidateQueries: vi.fn(async () => undefined),
  }),
}));

vi.mock("@angee/resources", () => ({
  refineInvalidationParams: (target: unknown) => target,
  resourceInvalidationTargets: () => [],
  useActiveGraphQLSchemaName: () => "console",
  useSchemaFieldMetadata: () => ({}),
}));

vi.mock("@angee/refine", () => ({
  authoredQueryMeta: (models: readonly string[]) => ({ models }),
  authoredQueryReadsAnyModel: () => false,
  useStableArray: <T,>(value: readonly T[]) => value,
  useStableVariables: <T,>(value: T) => value,
}));

beforeEach(() => {
  mutationMock.calls = [];
  mutationMock.generation = 0;
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
});
