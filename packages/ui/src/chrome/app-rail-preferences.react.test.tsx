// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider, type RuntimeUserPreferences } from "../runtime";
import {
  APP_RAIL_PREFERENCES_KEY,
  useAppRailPreferences,
} from "./app-rail-preferences";

describe("useAppRailPreferences", () => {
  test("keeps the newest optimistic write through stale storage and rejection", async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    const patchPreferences = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    let preferences: RuntimeUserPreferences = {};
    const wrapper = ({ children }: { children: ReactNode }) => (
      <AppRuntimeProvider
        runtime={{
          userPreferences: {
            available: true,
            preferences,
            patchPreferences,
          },
        }}
      >
        {children}
      </AppRuntimeProvider>
    );
    const { result, rerender } = renderHook(useAppRailPreferences, { wrapper });

    act(() => {
      result.current.setRailPreferences({
        order: ["notes", "ops"],
        defaultItemId: "notes",
        expanded: true,
      });
      result.current.setRailPreferences({
        order: ["ops", "notes"],
        defaultItemId: "ops",
        expanded: false,
      });
    });

    preferences = {
      [APP_RAIL_PREFERENCES_KEY]: {
        order: ["notes", "ops"],
        defaultItemId: "notes",
        expanded: true,
      },
    };
    rerender();
    expect(result.current.railPreferences).toEqual({
      order: ["ops", "notes"],
      defaultItemId: "ops",
      expanded: false,
    });

    await act(async () => {
      first.reject(new Error("stale write failed"));
      await first.promise.catch(() => undefined);
    });
    expect(result.current.railPreferences.defaultItemId).toBe("ops");

    await act(async () => {
      second.reject(new Error("latest write failed"));
      await second.promise.catch(() => undefined);
    });
    expect(result.current.railPreferences).toEqual({
      order: ["notes", "ops"],
      defaultItemId: "notes",
      expanded: true,
    });
  });
});

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}
