// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { useServiceLogStream } from "./logs";

vi.mock("../../data/transport", () => ({
  useOperatorConnection: () => ({
    endpoint: "http://daemon.test/graphql/",
    token: "TOKEN",
  }),
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

  emitLine(message: string): void {
    this.onmessage?.({ data: JSON.stringify({ message }) } as MessageEvent);
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

  test("leaves the backlog to the daemon, and a reconnect's replay replaces the buffer", async () => {
    const { result, unmount } = renderHook(() => useServiceLogStream("web"));
    const first = FakeWebSocket.instances[0]!;
    // The daemon refuses a follow backlog a local runtime cannot replay whole, and
    // replays its default on every follow: the console names no `tail`.
    expect(first.url).toBe("ws://daemon.test/services/web/logs/stream?token=TOKEN");

    act(() => {
      first.emitOpen();
      first.emitLine("boot");
      first.emitLine("ready");
    });
    expect(result.current.lines).toEqual(["boot", "ready"]);

    act(() => {
      first.emitClose(1006);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    const second = FakeWebSocket.instances[1]!;
    expect(second.url).toBe(first.url);
    // Old lines stay on screen until the new follow opens; then its replay is the buffer.
    expect(result.current.lines).toEqual(["boot", "ready"]);
    act(() => {
      second.emitOpen();
      second.emitLine("ready");
      second.emitLine("request served");
    });
    expect(result.current.lines).toEqual(["ready", "request served"]);

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
