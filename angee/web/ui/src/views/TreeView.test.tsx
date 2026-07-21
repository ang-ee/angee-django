// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider } from "../runtime";

import { baseIcons } from "../chrome/icon-registry";
import { TreeView } from "./TreeView";

afterEach(() => cleanup());

interface Row extends Record<string, unknown> {
  id: string;
  name: string;
  parentId: string;
  kids?: number;
  expandable?: boolean;
}

const ROWS: Row[] = [
  { id: "systems", name: "Systems", parentId: "" },
  { id: "graphrag", name: "Graph-RAG retrieval", parentId: "systems" },
  { id: "diagrams", name: "Diagrams", parentId: "systems", kids: 4 },
  { id: "reading", name: "Reading", parentId: "" },
];

function withIcons(node: React.ReactNode): React.ReactElement {
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons }}>{node}</AppRuntimeProvider>
  );
}

describe("TreeView", () => {
  test("folds parent-pointed rows into a tree and selects by row", () => {
    const onSelect = vi.fn();
    render(
      withIcons(
        <TreeView<Row> rows={ROWS} badge="kids" onSelect={onSelect} />,
      ),
    );
    // Roots and (expanded) children render.
    expect(screen.getByText("Systems")).toBeTruthy();
    expect(screen.getByText("Graph-RAG retrieval")).toBeTruthy();
    expect(screen.getByText("Reading")).toBeTruthy();
    // Badge count is shown.
    expect(screen.getByText("4")).toBeTruthy();

    fireEvent.click(screen.getByText("Graph-RAG retrieval"));
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ id: "graphrag" }),
    );
  });

  test("collapsing a folder hides its children", () => {
    render(withIcons(<TreeView<Row> rows={ROWS} />));
    expect(screen.getByText("Graph-RAG retrieval")).toBeTruthy();
    // The Systems row's caret collapses it.
    const systems = screen.getByText("Systems").closest('[role="treeitem"]');
    const caret = systems?.querySelector("button");
    fireEvent.click(caret!);
    expect(screen.queryByText("Graph-RAG retrieval")).toBeNull();
  });

  test("a `hasChildren` row with no loaded children carets and lazily expands", () => {
    const onExpand = vi.fn();
    // A single root row that declares it is expandable although its children
    // are not loaded (a lazy tree). It renders folded with an enabled caret.
    const lazyRows: Row[] = [
      { id: "root", name: "Lazy root", parentId: "", expandable: true },
    ];
    render(
      withIcons(
        <TreeView<Row>
          rows={lazyRows}
          hasChildren="expandable"
          onExpand={onExpand}
        />,
      ),
    );
    const root = screen.getByText("Lazy root").closest('[role="treeitem"]');
    const caret = root?.querySelector("button") as HTMLButtonElement;
    // Folded, so the caret offers to expand, not collapse.
    expect(caret.getAttribute("aria-label")).toBe("Expand");
    expect(caret.disabled).toBe(false);
    fireEvent.click(caret);
    expect(onExpand).toHaveBeenCalledWith("root");
  });

  test("hasChildren:false suppresses the caret; unset keeps loaded-children default", () => {
    // `reading` has no children and declares hasChildren:false → no caret.
    // `systems` leaves hasChildren unset but has loaded children → keeps its caret.
    const rows: Row[] = [
      { id: "systems", name: "Systems", parentId: "" },
      { id: "child", name: "Child", parentId: "systems" },
      { id: "reading", name: "Reading", parentId: "", expandable: false },
    ];
    render(withIcons(<TreeView<Row> rows={rows} hasChildren="expandable" />));
    const reading = screen.getByText("Reading").closest('[role="treeitem"]');
    expect(
      (reading?.querySelector("button") as HTMLButtonElement).disabled,
    ).toBe(true);
    // Systems (unset field) still expands its loaded child.
    expect(screen.getByText("Child")).toBeTruthy();
    const systems = screen.getByText("Systems").closest('[role="treeitem"]');
    expect(
      (systems?.querySelector("button") as HTMLButtonElement).disabled,
    ).toBe(false);
  });
});
