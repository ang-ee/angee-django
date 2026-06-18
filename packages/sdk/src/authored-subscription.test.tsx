// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { TypedDocumentNode } from "@urql/core";
import { parse } from "graphql";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { useAuthoredSubscription } from "./authored-hooks";

// A faithful `urql.useSubscription` stand-in: it runs the reducer once per *push*
// (threading the previous reducer result, as urql does), holds that result as state,
// and returns the same reference across bare re-renders. `emit` drives a push from
// the test; `afterReducer` fires right after the reducer and before the state flush,
// so a test can prove `onData` is not called from the reducer itself.
const sub = vi.hoisted(() => ({
  listeners: new Set<(value: unknown) => void>(),
  afterReducer: null as (() => void) | null,
  emit(value: unknown) {
    for (const listener of this.listeners) listener(value);
  },
}));

vi.mock("urql", () => ({
  Provider: ({ children }: { children: ReactNode }) => children,
  useMutation: () => [{ fetching: false, error: null }, vi.fn()],
  useQuery: () => [{ data: undefined, fetching: false, error: null }, vi.fn()],
  useSubscription: (
    _request: unknown,
    handler: (previous: unknown, value: unknown) => unknown,
  ) => {
    const handlerRef = useRef(handler);
    handlerRef.current = handler;
    const resultRef = useRef<unknown>(undefined);
    const [state, setState] = useState<{ data: unknown; fetching: boolean; error: null }>({
      data: undefined,
      fetching: true,
      error: null,
    });
    useEffect(() => {
      const onPush = (value: unknown) => {
        resultRef.current = handlerRef.current(resultRef.current, value);
        sub.afterReducer?.();
        setState({ data: resultRef.current, fetching: false, error: null });
      };
      sub.listeners.add(onPush);
      return () => {
        sub.listeners.delete(onPush);
      };
    }, []);
    return [state];
  },
}));

function typedDocument<TData, TVariables extends Record<string, unknown>>(
  source: string,
): TypedDocumentNode<TData, TVariables> {
  return parse(source) as TypedDocumentNode<TData, TVariables>;
}

describe("useAuthoredSubscription", () => {
  beforeEach(() => {
    sub.afterReducer = null;
  });

  afterEach(() => {
    cleanup();
    sub.listeners.clear();
  });

  test("fires onData from an effect after the push, never from the reducer", async () => {
    const onData = vi.fn();
    const document = typedDocument<{ ping: string }, Record<string, never>>(
      "subscription { ping }",
    );
    // The reducer has just run; the effect that fires onData has not flushed yet.
    sub.afterReducer = () => expect(onData).not.toHaveBeenCalled();

    renderHook(() =>
      useAuthoredSubscription(document, undefined, { onData }),
    );
    expect(onData).not.toHaveBeenCalled(); // nothing fires before the first push

    act(() => sub.emit({ ping: "pong" }));
    await waitFor(() => expect(onData).toHaveBeenCalledWith({ ping: "pong" }));
  });

  test("fires onData once per push — not on bare re-renders", async () => {
    const onData = vi.fn();
    const document = typedDocument<{ ping: string }, Record<string, never>>(
      "subscription { ping }",
    );
    const { rerender } = renderHook(() =>
      useAuthoredSubscription(document, undefined, { onData }),
    );

    act(() => sub.emit({ ping: "one" }));
    await waitFor(() => expect(onData).toHaveBeenCalledTimes(1));
    expect(onData).toHaveBeenLastCalledWith({ ping: "one" });

    // A re-render with no new push must not re-fire onData (per-push, not per-render).
    rerender();
    expect(onData).toHaveBeenCalledTimes(1);

    // A second, distinct push bumps the event version and fires onData again.
    act(() => sub.emit({ ping: "two" }));
    await waitFor(() => expect(onData).toHaveBeenCalledTimes(2));
    expect(onData).toHaveBeenLastCalledWith({ ping: "two" });
  });
});
