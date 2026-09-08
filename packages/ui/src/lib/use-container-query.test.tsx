// @vitest-environment happy-dom

import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { useContainerQuery } from "./use-container-query";

let observerCallback: ResizeObserverCallback;
const disconnect = vi.fn();

beforeEach(() => {
  disconnect.mockClear();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        observerCallback = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {
        disconnect();
      }
    },
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("tracks container width and disconnects the observer", () => {
  const element = document.createElement("div");
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    width: 500,
  } as DOMRect);
  const { result, unmount } = renderHook(() => useContainerQuery(760));

  act(() => result.current[0](element));
  expect(result.current[1]).toBe(false);

  act(() => {
    observerCallback(
      [{ contentRect: { width: 900 } as DOMRectReadOnly } as ResizeObserverEntry],
      {} as ResizeObserver,
    );
  });
  expect(result.current[1]).toBe(true);

  unmount();
  expect(disconnect).toHaveBeenCalledOnce();
});
