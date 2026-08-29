// @vitest-environment happy-dom
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { useBusyRun } from "./use-busy-run";

describe("useBusyRun", () => {
  test("returns the action's value and fires onChanged once on success", async () => {
    const onChanged = vi.fn();
    const { result } = renderHook(() => useBusyRun(onChanged));
    expect(result.current.busy).toBe(false);

    let value: string | undefined;
    await act(async () => {
      value = await result.current.run(async () => "node-1");
    });

    expect(value).toBe("node-1");
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(result.current.busy).toBe(false);
  });

  test("is busy while the action is in flight, then clears", async () => {
    const { result } = renderHook(() => useBusyRun());
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });

    let pending!: Promise<void>;
    act(() => {
      pending = result.current.run(() => gate);
    });
    await waitFor(() => expect(result.current.busy).toBe(true));

    await act(async () => {
      release();
      await pending;
    });
    expect(result.current.busy).toBe(false);
  });

  test("stays busy until every overlapping action settles", async () => {
    const { result } = renderHook(() => useBusyRun());
    let releaseFirst!: () => void;
    let releaseSecond!: () => void;
    const first = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const second = new Promise<void>((resolve) => {
      releaseSecond = resolve;
    });

    let firstRun!: Promise<void>;
    let secondRun!: Promise<void>;
    act(() => {
      firstRun = result.current.run(() => first);
      secondRun = result.current.run(() => second);
    });
    await waitFor(() => expect(result.current.busy).toBe(true));

    await act(async () => {
      releaseFirst();
      await firstRun;
    });
    expect(result.current.busy).toBe(true);

    await act(async () => {
      releaseSecond();
      await secondRun;
    });
    expect(result.current.busy).toBe(false);
  });

  test("clears busy and re-throws without firing onChanged on failure", async () => {
    const onChanged = vi.fn();
    const { result } = renderHook(() => useBusyRun(onChanged));

    await act(async () => {
      await expect(
        result.current.run(async () => {
          throw new Error("boom");
        }),
      ).rejects.toThrow("boom");
    });

    expect(onChanged).not.toHaveBeenCalled();
    expect(result.current.busy).toBe(false);
  });
});
