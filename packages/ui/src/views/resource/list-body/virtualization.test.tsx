// @vitest-environment happy-dom

import * as React from "react";
import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { useResourceViewVirtualizer } from "../surface/presentation";
import { useVirtualWindow } from "./virtualization";

test("a body's window reads the count and size estimate its surface gave the virtualizer", () => {
  const windows: ReturnType<typeof useVirtualWindow>[] = [];
  function Body(): React.ReactElement {
    const { tableScrollRef, rowVirtualizer } = useResourceViewVirtualizer(30, (index) => (index < 25 ? 40 : 100));
    windows.push(useVirtualWindow(rowVirtualizer));
    return <div ref={tableScrollRef} />;
  }
  render(<Body />);
  // The document has no layout: the scroll element the ref attached measures no height, so
  // there is no window, and the first twenty items render with the other ten padded by
  // the surface's estimate of each.
  expect(windows.at(-1)!.visibleIndexes).toEqual([...Array(20).keys()]);
  expect(windows.at(-1)!.paddingBottom).toBe(5 * 40 + 5 * 100);
});
