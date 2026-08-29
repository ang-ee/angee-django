// @vitest-environment happy-dom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { parse } from "graphql";
import { afterEach, describe, expect, test, vi } from "vitest";

import { useDocumentSubscription } from "./document-subscription";

interface Sink {
  next: (value: { data: unknown }) => void;
  error: (error: unknown) => void;
  complete: () => void;
}

// One fake daemon ws client: `subscribe` registers the sink and `emit` pushes a
// frame to every open sink, the same shape graphql-ws delivers to `next`. The
// client reference is stable (as the real context value is), so the hook's
// `[client]` effect does not re-run every render.
const ws = vi.hoisted(() => {
  const sinks = new Set<Sink>();
  return {
    sinks,
    client: {
      subscribe: (_payload: unknown, sink: Sink) => {
        sinks.add(sink);
        return () => {
          sinks.delete(sink);
        };
      },
    },
    emit(value: unknown) {
      for (const sink of sinks) sink.next({ data: value });
    },
  };
});

vi.mock("./operator-client", () => ({
  useOperatorWsClient: () => ws.client,
}));

describe("useDocumentSubscription", () => {
  afterEach(() => {
    cleanup();
    ws.sinks.clear();
  });

  test("fires onData once per push over the daemon ws client", async () => {
    const onData = vi.fn();
    const document = parse("subscription { ping }");

    renderHook(() =>
      useDocumentSubscription<{ ping: string }, Record<string, never>>(
        document,
        undefined,
        { onData },
      ),
    );
    expect(onData).not.toHaveBeenCalled();

    act(() => ws.emit({ ping: "pong" }));
    await waitFor(() => expect(onData).toHaveBeenCalledWith({ ping: "pong" }));
    expect(onData).toHaveBeenCalledTimes(1);
  });

  test("fires onData per push, not on bare rerenders", async () => {
    const onData = vi.fn();
    const document = parse("subscription { ping }");
    const { rerender } = renderHook(() =>
      useDocumentSubscription<{ ping: string }, Record<string, never>>(
        document,
        undefined,
        { onData },
      ),
    );

    act(() => ws.emit({ ping: "one" }));
    await waitFor(() => expect(onData).toHaveBeenCalledTimes(1));
    expect(onData).toHaveBeenLastCalledWith({ ping: "one" });

    rerender();
    expect(onData).toHaveBeenCalledTimes(1);

    act(() => ws.emit({ ping: "two" }));
    await waitFor(() => expect(onData).toHaveBeenCalledTimes(2));
    expect(onData).toHaveBeenLastCalledWith({ ping: "two" });
  });
});
