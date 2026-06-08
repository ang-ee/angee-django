// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider } from "@angee/sdk";

import { baseIcons } from "../chrome/icon-registry";
import { TreeView } from "./TreeView";

afterEach(() => cleanup());

interface Row extends Record<string, unknown> {
  id: string;
  name: string;
  parentId: string;
  kids?: number;
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
});
