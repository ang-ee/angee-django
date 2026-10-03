// @vitest-environment happy-dom

import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { useOverflowCount } from "./use-overflow-count";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("reserves gaps and a wider swapped item, responds to item resizing, and disconnects", () => {
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
    const [host, measurement, count] = useOverflowCount(entries, keepIndex);
    return <div id="host" ref={host}>
      <div ref={measurement} style={{ columnGap: "10px" }}>
        <span />{entries.map((id) => <span key={id} id={id === "d" ? "kept" : id} />)}<span />
      </div>
      <output>{count}</output>
    </div>;
  }
  const { rerender, unmount } = render(<Host keepIndex={-1} />);
  expect(screen.getByRole("status").textContent).toBe("3");
  rerender(<Host keepIndex={3} />);
  expect(screen.getByRole("status").textContent).toBe("1");
  expect(observed.some((element) => element.id === "kept")).toBe(true);
  keptWidth = 60;
  act(() => callback!([], {} as ResizeObserver));
  expect(screen.getByRole("status").textContent).toBe("4");
  containerWidth = 100;
  act(() => callback!([{
    target: document.getElementById("host")!, contentRect: { width: 100 } as DOMRectReadOnly,
    borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [],
  }], {} as ResizeObserver));
  expect(screen.getByRole("status").textContent).toBe("0");
  unmount();
  expect(disconnect).toHaveBeenCalledTimes(2);
});
