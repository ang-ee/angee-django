// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("../../layouts/primary-pane-context", () => ({
  PrimaryPanePublisher: ({ node }: { node: ReactNode }) => node,
}));

import { ScopedExplorerPane } from "./ScopedExplorerPane";

afterEach(cleanup);

const emptyExplorer = {
  roots: [],
  getRootId: (root: { id: string }) => root.id,
  getRootLabel: (root: { id: string }) => root.id,
  getTreeRows: () => [] as Array<{ id: string }>,
  navigatorLabel: "Folders",
  rootPicker: { "aria-label": "Drive" },
  renderTree: () => null,
};

test("renders caller content without roots only when explicitly requested", () => {
  const { rerender } = render(
    <ScopedExplorerPane {...emptyExplorer}>
      {() => <div>Owned content</div>}
    </ScopedExplorerPane>,
  );

  expect(screen.queryByText("Owned content")).toBeNull();

  rerender(
    <ScopedExplorerPane {...emptyExplorer} renderContentWithoutRoots>
      {() => <div>Owned content</div>}
    </ScopedExplorerPane>,
  );

  expect(screen.getByText("Owned content")).toBeTruthy();
});

test("a root search stays mounted with no options while pending, empty or failed", async () => {
  const search = vi.fn();
  const retry = vi.fn();
  const rootPicker = { "aria-label": "Drive", searchPlaceholder: "Search drives", onSearchChange: search };
  const content = <ScopedExplorerPane {...emptyExplorer}
    renderTree={(_controller, selector) => selector} rootPicker={rootPicker}>
    {() => <div>Owned content</div>}
  </ScopedExplorerPane>;
  const { rerender } = render(content);
  fireEvent.click(screen.getByRole("combobox", { name: "Drive" }));
  const input = await screen.findByPlaceholderText("Search drives");
  fireEvent.change(input, { target: { value: "missing" } });
  expect(search).toHaveBeenCalledWith("missing");
  rerender(<ScopedExplorerPane {...emptyExplorer} loading
    renderTree={(_controller, selector) => selector} rootPicker={{ ...rootPicker, searchState: { pending: true } }}>
    {() => <div>Owned content</div>}
  </ScopedExplorerPane>);
  expect(screen.getByPlaceholderText("Search drives")).toBe(input);
  rerender(<ScopedExplorerPane {...emptyExplorer}
    renderTree={(_controller, selector) => selector} rootPicker={{ ...rootPicker, searchState: { error: "Unavailable", retry } }}>
    {() => <div>Owned content</div>}
  </ScopedExplorerPane>);
  expect(screen.getByPlaceholderText("Search drives")).toBe(input);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  expect(retry).toHaveBeenCalledOnce();
  fireEvent.change(input, { target: { value: "" } });
  expect(search).toHaveBeenLastCalledWith("");
});
