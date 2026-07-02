// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import type { ActionContext } from "./page";
import {
  recordActionId,
  useRecordAction,
  useRecordActionMutation,
} from "./record-action";

const dataMocks = vi.hoisted(() => ({
  mutate: vi.fn(async () => "Synced"),
  useActionMutation: vi.fn(),
}));

vi.mock("@angee/refine", () => ({
  useActionMutation: dataMocks.useActionMutation,
}));

vi.mock("@angee/metadata", () => ({
  refineInvalidationParams: (target: { modelLabel: string }) => ({
    dataProviderName: "console",
    invalidates: ["list"],
    resource: target.modelLabel,
  }),
  resourceInvalidationTargets: (_metadata: unknown, modelLabels: readonly string[]) =>
    modelLabels.map((modelLabel) => ({ modelLabel })),
  useSchemaFieldMetadata: () => ({ schemas: {} }),
}));

describe("record action helpers", () => {
  beforeEach(() => {
    dataMocks.mutate.mockClear();
    dataMocks.useActionMutation.mockReset();
    dataMocks.useActionMutation.mockReturnValue([
      dataMocks.mutate,
      { fetching: false, error: null },
    ]);
  });

  test("reads a saved record id from the action context", () => {
    expect(recordActionId(actionContext("row-1"))).toBe("row-1");
    expect(recordActionId(actionContext(""))).toBeUndefined();
    expect(recordActionId(actionContext(undefined))).toBeUndefined();
  });

  test("runs by record id, refreshes, and returns the action message", async () => {
    const refresh = vi.fn();
    const run = vi.fn(async () => "Synced");
    const { result } = renderHook(() => useRecordAction(run));

    let message: string | void = undefined;
    await act(async () => {
      message = await result.current(actionContext("row-1", { refresh }));
    });

    expect(run).toHaveBeenCalledWith(
      "row-1",
      expect.objectContaining({ record: { id: "row-1" } }),
    );
    expect(refresh).toHaveBeenCalledOnce();
    expect(message).toBe("Synced");
  });

  test("uses the default message when the action returns no message", async () => {
    const { result } = renderHook(() =>
      useRecordAction(async () => undefined, { defaultMessage: "Done" }),
    );

    let message: string | void = undefined;
    await act(async () => {
      message = await result.current(actionContext("row-1"));
    });

    expect(message).toBe("Done");
  });

  test("runs afterSuccess after the form refresh", async () => {
    const calls: string[] = [];
    const { result } = renderHook(() =>
      useRecordAction(async () => "Done", {
        afterSuccess: () => {
          calls.push("afterSuccess");
        },
      }),
    );

    await act(async () => {
      await result.current(
        actionContext("row-1", {
          refresh: () => {
            calls.push("refresh");
          },
        }),
      );
    });

    expect(calls).toEqual(["refresh", "afterSuccess"]);
  });

  test("throws the configured missing-record message", async () => {
    const { result } = renderHook(() =>
      useRecordAction(async () => "Done", {
        missingRecordMessage: "Save first",
      }),
    );

    await expect(result.current(actionContext(undefined))).rejects.toThrow(
      "Save first",
    );
  });

  test("passes invalidation targets to the data action owner", async () => {
    const refresh = vi.fn();
    const { result } = renderHook(() =>
      useRecordActionMutation("refresh_source", {
        invalidateModels: ["agents.Skill"],
      }),
    );

    await act(async () => {
      await result.current[0](actionContext("src_1", { refresh }));
    });

    expect(dataMocks.useActionMutation).toHaveBeenCalledWith(
      "refresh_source",
      {
        invalidates: [
          {
            dataProviderName: "console",
            invalidates: ["list"],
            resource: "agents.Skill",
          },
        ],
      },
    );
    expect(dataMocks.mutate).toHaveBeenCalledWith("src_1");
    expect(refresh).toHaveBeenCalledOnce();
  });
});

function actionContext(
  id: string | undefined,
  overrides: Partial<ActionContext> = {},
): ActionContext {
  return {
    record: id === undefined ? null : { id },
    values: {},
    refresh: vi.fn(),
    update: vi.fn(),
    prompt: vi.fn(),
    ...overrides,
  };
}
