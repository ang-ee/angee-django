// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import type { ActionContext } from "./page";
import { recordActionId, useRecordAction } from "./record-action";

describe("record action helpers", () => {
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
