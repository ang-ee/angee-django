// @vitest-environment happy-dom
import { act, renderHook } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { describe, expect, test, vi } from "vitest";

import {
  changeSubscriptionFields,
  RelayInvalidationProvider,
  useInvalidateModels,
  useModelInvalidation,
  useRegisterModelRefetch,
} from "./relay-invalidation";

// autoSubscribe is off so the test exercises the registry wiring without opening
// a live change-event WebSocket.
function wrapper({ children }: { children: ReactNode }) {
  return createElement(
    RelayInvalidationProvider,
    { autoSubscribe: false, children },
  );
}

describe("relay invalidation wiring", () => {
  test("a registered refetch fires when its model is invalidated", () => {
    const refetch = vi.fn();
    const { result } = renderHook(
      () => {
        useRegisterModelRefetch("notes.Note", refetch, true);
        return useInvalidateModels();
      },
      { wrapper },
    );
    act(() => result.current(["notes.Note"]));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("useModelInvalidation invalidates one model imperatively", () => {
    const refetch = vi.fn();
    const { result } = renderHook(
      () => {
        useRegisterModelRefetch("notes.Note", refetch, true);
        return useModelInvalidation("notes.Note");
      },
      { wrapper },
    );
    act(() => result.current());
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  test("a disabled registration does not fire", () => {
    const refetch = vi.fn();
    const { result } = renderHook(
      () => {
        useRegisterModelRefetch("notes.Note", refetch, false);
        return useInvalidateModels();
      },
      { wrapper },
    );
    act(() => result.current(["notes.Note"]));
    expect(refetch).not.toHaveBeenCalled();
  });
});

describe("change subscription gating", () => {
  test("returns the change fields the schema's Subscription type defines", () => {
    const fields = changeSubscriptionFields(`
      type Query { ok: Boolean }
      type ChangeEvent { model: String }
      type Subscription { noteChanged: ChangeEvent fileChanged: ChangeEvent }
    `);
    expect(fields.has("noteChanged")).toBe(true);
    expect(fields.has("fileChanged")).toBe(true);
    // A model without a `changes()` field is not gated in — no blind subscription.
    expect(fields.has("skillChanged")).toBe(false);
  });

  test("is empty when the schema declares no Subscription type", () => {
    expect(changeSubscriptionFields(`type Query { ok: Boolean }`).size).toBe(0);
  });
});
