// @vitest-environment happy-dom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useOverflowCount } from "./use-overflow-count";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("partitions entries with the container gap, keeps border-box measurements on resize, and reserves the current item", () => {
  let callback: ResizeObserverCallback;
  const observed: Element[] = [];
  const disconnect = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(next: ResizeObserverCallback) { callback = next; }
    observe(element: Element) { observed.push(element); }
    disconnect() { disconnect(); }
  });
  let containerWidth = 380;
  let keptWidth = 180;
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return { width: this.id === "host" ? containerWidth : this.id === "kept" ? keptWidth : 60 } as DOMRect;
  });
  const entries = ["a", "b", "c", "d"];
  function Host({ keepIndex }: { keepIndex: number }) {
    const [host, measurement, visible, overflow] = useOverflowCount(entries, keepIndex);
    return <div id="host" ref={host} style={{ columnGap: "10px", border: "4px solid", padding: "6px" }}>
      <div ref={measurement} style={{ columnGap: "40px" }}>
        <span />{entries.map((id) => <span key={id} id={id === "d" ? "kept" : id} />)}<span />
      </div>
      <output>{JSON.stringify({ visible, overflow })}</output>
    </div>;
  }
  const { rerender, unmount } = render(<Host keepIndex={-1} />);
  const partition = () => JSON.parse(screen.getByRole("status").textContent!);
  expect(partition()).toEqual({ visible: [0, 1, 2], overflow: [3] });
  containerWidth = 355;
  act(() => callback!([{
    target: document.getElementById("host")!, contentRect: { width: 335 } as DOMRectReadOnly,
    borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [],
  }], {} as ResizeObserver));
  expect(partition()).toEqual({ visible: [0, 1], overflow: [2, 3] });
  // An item-only notification must use the same available width as a container notification.
  act(() => callback!([], {} as ResizeObserver));
  expect(partition()).toEqual({ visible: [0, 1], overflow: [2, 3] });
  containerWidth = 380;
  rerender(<Host keepIndex={3} />);
  expect(partition()).toEqual({ visible: [3], overflow: [0, 1, 2] });
  expect(observed.some((element) => element.id === "kept")).toBe(true);
  keptWidth = 60;
  act(() => callback!([], {} as ResizeObserver));
  expect(partition()).toEqual({ visible: [0, 1, 2, 3], overflow: [] });
  containerWidth = 100;
  act(() => callback!([{
    target: document.getElementById("host")!, contentRect: { width: 100 } as DOMRectReadOnly,
    borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [],
  }], {} as ResizeObserver));
  expect(partition()).toEqual({ visible: [], overflow: [0, 1, 2, 3] });
  unmount();
  expect(disconnect).toHaveBeenCalledTimes(2);
});
