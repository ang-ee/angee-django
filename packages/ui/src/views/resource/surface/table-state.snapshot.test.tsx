// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { useResourceRowsSnapshot } from "./table-state";

afterEach(cleanup);

const source = (rows: readonly { id: string }[], over: Record<string, unknown> = {}) => ({
  rows,
  total: rows.length,
  page: 1,
  pageSize: 50,
  pageCount: 1,
  hasNext: false,
  hasPrev: false,
  fetching: false,
  error: undefined,
  ...over,
});

function Harness({
  rows,
  onListStateChange,
  navigation,
}: {
  rows: readonly { id: string }[];
  onListStateChange: (state: unknown) => void;
  navigation?: { filter?: unknown; order?: unknown };
}): null {
  useResourceRowsSnapshot(source(rows) as never, {
    onListStateChange: onListStateChange as never,
    ...(navigation ? { navigation: navigation as never } : {}),
  });
  return null;
}

describe("useResourceRowsSnapshot", () => {
  test("tells the consumer once for a list whose content has not changed", () => {
    // The board rebuilt its rows array on every render, so an identity-keyed
    // effect fired ~60 times per load. Each run called two setters; React can
    // only skip scheduling a same-value update while the fiber has no pending
    // lane, so after the first one every later call still counted as a nested
    // update -- "Maximum update depth exceeded" on board load.
    const onListStateChange = vi.fn();
    const view = render(
      <Harness rows={[{ id: "a" }, { id: "b" }]} onListStateChange={onListStateChange} />,
    );
    expect(onListStateChange).toHaveBeenCalledTimes(1);

    // Same content, new array and new row objects, and a new callback identity —
    // all three of the things that churned on a real board render.
    for (let i = 0; i < 5; i += 1) {
      view.rerender(
        <Harness rows={[{ id: "a" }, { id: "b" }]} onListStateChange={onListStateChange} />,
      );
    }
    expect(onListStateChange).toHaveBeenCalledTimes(1);
  });

  test("still tells the consumer when the content really changes", () => {
    const onListStateChange = vi.fn();
    const view = render(
      <Harness rows={[{ id: "a" }]} onListStateChange={onListStateChange} />,
    );
    expect(onListStateChange).toHaveBeenCalledTimes(1);

    view.rerender(<Harness rows={[{ id: "a" }, { id: "b" }]} onListStateChange={onListStateChange} />);
    expect(onListStateChange).toHaveBeenCalledTimes(2);

    view.rerender(<Harness rows={[{ id: "b" }]} onListStateChange={onListStateChange} />);
    expect(onListStateChange).toHaveBeenCalledTimes(3);
  });
});
