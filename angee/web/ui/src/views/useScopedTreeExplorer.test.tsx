// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, test } from "vitest";

import { useScopedTreeExplorer } from "./useScopedTreeExplorer";

interface Root {
  id: string;
  name: string;
}

interface TreeRow {
  id: string;
  parent: string | null;
  title: string;
}

const ROOTS: readonly Root[] = [
  { id: "root-a", name: "Root A" },
  { id: "root-b", name: "Root B" },
];

const ROWS: Record<string, readonly TreeRow[]> = {
  "root-a": [
    { id: "a-folder", parent: null, title: "Folder A" },
    { id: "a-child", parent: "a-folder", title: "Child A" },
  ],
  "root-b": [
    { id: "b-folder", parent: null, title: "Folder B" },
  ],
};

describe("useScopedTreeExplorer", () => {
  test("defaults to the first root and projects root options", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getRootLabel,
        getTreeRows,
      }));

    expect(result.current.rootId).toBe("root-a");
    expect(result.current.root).toEqual(ROOTS[0]);
    expect(result.current.rootOptions).toEqual([
      { value: "root-a", label: "Root A" },
      { value: "root-b", label: "Root B" },
    ]);
    expect(result.current.treeRows).toEqual(ROWS["root-a"]);
  });

  test("uses the selected root hint until a root is pinned", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getRootLabel,
        getTreeRows,
        selectedId: "b-folder",
        selectedRootId: "root-b",
      }));

    expect(result.current.rootId).toBe("root-b");
    expect(result.current.selectedId).toBe("b-folder");
    expect(result.current.selectedRow).toEqual(ROWS["root-b"]?.[0]);

    act(() => result.current.setRootId("root-a"));

    expect(result.current.rootId).toBe("root-a");
    expect(result.current.selectedId).toBeUndefined();
  });

  test("resets uncontrolled selection to the default when the root changes", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getRootLabel,
        getTreeRows,
        defaultSelectedId: "all",
        isSelectedIdValid: hasPseudoSelection,
      }));

    act(() => result.current.setSelectedId("a-folder"));
    expect(result.current.selectedId).toBe("a-folder");
    expect(result.current.selectedRow).toEqual(ROWS["root-a"]?.[0]);

    act(() => result.current.setRootId("root-b"));

    expect(result.current.rootId).toBe("root-b");
    expect(result.current.selectedId).toBe("all");
    expect(result.current.selectedRow).toBeUndefined();
  });

  test("clamps invalid uncontrolled selections to the default", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getRootLabel,
        getTreeRows,
        defaultSelectedId: "all",
        isSelectedIdValid: hasPseudoSelection,
      }));

    act(() => result.current.setSelectedId("missing-folder"));

    expect(result.current.selectedId).toBe("all");
    expect(result.current.selectedRow).toBeUndefined();
  });

  test("keeps an unavailable pinned root until options include it", () => {
    let roots: readonly Root[] = [ROOTS[0] as Root];
    const { result, rerender } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots,
        getRootId,
        getRootLabel,
        getTreeRows,
      }));

    act(() => result.current.setRootId("root-b"));
    expect(result.current.rootId).toBe("root-a");

    roots = ROOTS;
    rerender();

    expect(result.current.rootId).toBe("root-b");
    expect(result.current.treeRows).toEqual(ROWS["root-b"]);
  });
});

function getRootId(root: Root): string {
  return root.id;
}

function getRootLabel(root: Root): string {
  return root.name;
}

function getTreeRows(rootId: string): readonly TreeRow[] {
  return ROWS[rootId] ?? [];
}

function hasPseudoSelection(
  selectedId: string,
  rows: readonly TreeRow[],
): boolean {
  return selectedId === "all" || rows.some((row) => row.id === selectedId);
}
