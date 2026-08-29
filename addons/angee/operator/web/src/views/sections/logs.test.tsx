// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { useServiceLogStream } from "./logs";

vi.mock("../../data/transport", () => ({
  useOperatorConnection: () => ({
    endpoint: "http://daemon.test/graphql/",
    token: "TOKEN",
  }),
  useOperatorSubscription: vi.fn(),
}));

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  close(): void {}

  emitOpen(): void {
    this.onopen?.(new Event("open"));
  }

  emitClose(code: number): void {
    this.onclose?.({ code } as CloseEvent);
  }
}

describe("useServiceLogStream", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", FakeWebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("reconnects after a normal service log close", async () => {
    const { result, unmount } = renderHook(() => useServiceLogStream("web"));
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      FakeWebSocket.instances[0]?.emitOpen();
    });
    expect(result.current.streaming).toBe(true);

    act(() => {
      FakeWebSocket.instances[0]?.emitClose(1000);
    });
    expect(result.current.streaming).toBe(false);
    expect(result.current.error).toBeNull();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);

    unmount();
  });

  test("does not reconnect after an auth or policy close", async () => {
    const { result, unmount } = renderHook(() => useServiceLogStream("web"));
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      FakeWebSocket.instances[0]?.emitOpen();
      FakeWebSocket.instances[0]?.emitClose(4403);
    });

    expect(result.current.streaming).toBe(false);
    expect(result.current.error?.message).toBe("Log stream closed (4403)");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);

    unmount();
  });
});
