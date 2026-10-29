// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";

import { PaneToolbar, PaneToolbarHost } from "./PaneToolbar";

afterEach(cleanup);

test("multiple pane toolbars remain intrinsic rows when they fall back inline above a tree", () => {
  render(
    <PaneToolbarHost>
      <div data-testid="navigator" className="flex min-h-0 flex-1 flex-col">
        <PaneToolbar selector={<span>Assets</span>} />
        <span>All files</span>
        <PaneToolbar overflow />
        <div className="min-h-0 flex-1">Folders</div>
      </div>
    </PaneToolbarHost>,
  );

  const navigator = screen.getByTestId("navigator");
  const toolbars = screen.getAllByRole("toolbar");
  expect(toolbars).toHaveLength(2);
  expect(toolbars[0]?.contains(screen.getByText("Assets"))).toBe(true);
  expect(toolbars[1]?.contains(screen.getByRole("button", { name: "More" }))).toBe(true);
  for (const toolbar of toolbars) {
    expect(toolbar.parentElement).toBe(navigator);
    // Width fills the pane; main-axis growth would take height away from the tree.
    expect(toolbar.classList.contains("w-full")).toBe(true);
    expect(toolbar.classList.contains("flex-1")).toBe(false);
    expect(toolbar.classList.contains("shrink-0")).toBe(true);
  }
});
