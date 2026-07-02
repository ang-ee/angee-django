// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { ReactNode } from "react";

import { ActiveDataProviderNameProvider } from "./data-provider-context";
import { useAuthoredMutation } from "./authored-hooks";

const mutationMock = vi.hoisted(() => ({
  calls: [] as Array<{
    dataProviderName: string;
    generation: number;
    values: Record<string, unknown>;
  }>,
  data: undefined as unknown,
  generation: 0,
}));

const invalidationMock = vi.hoisted(() => ({
  authoredPredicateMatches: [] as unknown[],
  queryInvalidations: 0,
  resourceInvalidations: [] as unknown[],
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
              data: mutationMock.data ?? { generation, variables: payload.values },
            },
          };
        },
      ),
      mutation: { isPending: false, error: null },
    };
  },
  useInvalidate: () =>
    vi.fn(async (target: unknown) => {
      invalidationMock.resourceInvalidations.push(target);
    }),
}));

vi.mock("@tanstack/react-query", () => ({
  hashKey: (value: unknown) => JSON.stringify(value),
  useQueryClient: () => ({
    invalidateQueries: vi.fn(
      async (options: { predicate?: (query: { meta: unknown }) => boolean }) => {
        invalidationMock.queryInvalidations += 1;
        invalidationMock.authoredPredicateMatches.push(
          options.predicate?.({
            meta: { angeeModels: ["messaging.ThreadFollower"] },
          }),
          options.predicate?.({ meta: { angeeModels: ["other.Model"] } }),
        );
      },
    ),
  }),
}));

beforeEach(() => {
  mutationMock.calls = [];
  mutationMock.data = undefined;
  mutationMock.generation = 0;
  invalidationMock.authoredPredicateMatches = [];
  invalidationMock.queryInvalidations = 0;
  invalidationMock.resourceInvalidations = [];
});

describe("useAuthoredMutation", () => {
  test("keeps mutate identity stable while calling the latest refine mutation", async () => {
    const document = "mutation Probe { probe }" as never;
    const { result, rerender } = renderHook(
      () => useAuthoredMutation(document),
      { wrapper: ConsoleProvider },
    );
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

  test("invalidates authored reads by model labels only", async () => {
    const document = "mutation Probe { probe }" as never;
    const { result } = renderHook(
      () =>
        useAuthoredMutation(document, {
          invalidateModels: [
            "notes.Note",
            "messaging.ThreadFollower",
            "messaging.ThreadActivity",
          ],
        }),
      { wrapper: ConsoleProvider },
    );

    await act(async () => {
      await result.current[0]({ value: "fresh" } as never);
    });

    expect(invalidationMock.resourceInvalidations).toEqual([]);
    expect(invalidationMock.queryInvalidations).toBe(1);
    expect(invalidationMock.authoredPredicateMatches).toEqual([true, false]);
  });

  test("runs caller-prepared resource invalidations without model labels", async () => {
    const document = "mutation Probe { probe }" as never;
    const target = {
      resource: "notes",
      dataProviderName: "console",
      invalidates: ["list"],
    };
    const { result } = renderHook(
      () => useAuthoredMutation(document, { invalidates: [target as never] }),
      { wrapper: ConsoleProvider },
    );

    await act(async () => {
      await result.current[0]({ value: "fresh" } as never);
    });

    expect(invalidationMock.resourceInvalidations).toEqual([target]);
    expect(invalidationMock.queryInvalidations).toBe(0);
  });

  test("throws result envelope errors before invalidating", async () => {
    mutationMock.data = {
      run_action: {
        error_code: "DENIED",
        error: "Permission denied",
      },
    };
    const document = "mutation Probe { run_action { error error_code } }" as never;
    const { result } = renderHook(
      () =>
        useAuthoredMutation(document, {
          invalidateModels: ["messaging.ThreadFollower"],
          errorFrom: (data: any) => data?.run_action,
        }),
      { wrapper: ConsoleProvider },
    );

    await expect(
      act(async () => {
        await result.current[0]({ value: "fresh" } as never);
      }),
    ).rejects.toThrow("Permission denied");

    expect(invalidationMock.resourceInvalidations).toEqual([]);
    expect(invalidationMock.queryInvalidations).toBe(0);
  });
});

function ConsoleProvider({ children }: { children: ReactNode }) {
  return (
    <ActiveDataProviderNameProvider name="console">
      {children}
    </ActiveDataProviderNameProvider>
  );
}
