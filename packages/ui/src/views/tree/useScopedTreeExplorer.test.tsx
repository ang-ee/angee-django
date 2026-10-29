// @vitest-environment happy-dom

import { act, renderHook } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

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
  test("defaults to the first root", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getTreeRows,
      }));

    expect(result.current.rootId).toBe("root-a");
    expect(result.current.treeRows).toEqual(ROWS["root-a"]);
  });

  test("uses a resolved selected root until a root is picked", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
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

  test("routes setSelectedId to onSelectedIdChange for a controlled selection", () => {
    const onSelectedIdChange = vi.fn();
    const { result, rerender } = renderHook(
      ({ selectedId }: { selectedId: string }) =>
        useScopedTreeExplorer<Root, TreeRow>({
          roots: ROOTS,
          getRootId,
            getTreeRows,
          selectedId,
          onSelectedIdChange,
          defaultSelectedId: "all",
          isSelectedIdValid: hasPseudoSelection,
        }),
      { initialProps: { selectedId: "a-folder" } },
    );

    expect(result.current.selectedId).toBe("a-folder");

    act(() => result.current.setSelectedId("all"));

    // The controlled owner is notified; the controlled value only changes once the
    // owner feeds a new `selectedId` back in (here, the URL store round-trip).
    expect(onSelectedIdChange).toHaveBeenCalledWith("all");
    expect(result.current.selectedId).toBe("a-folder");

    rerender({ selectedId: "all" });
    expect(result.current.selectedId).toBe("all");
  });

  test("resets a controlled selection through onSelectedIdChange when the root changes", () => {
    const onSelectedIdChange = vi.fn();
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getTreeRows,
        selectedId: "a-folder",
        onSelectedIdChange,
        defaultSelectedId: "all",
        isSelectedIdValid: hasPseudoSelection,
      }));

    expect(result.current.selectedId).toBe("a-folder");

    act(() => result.current.setRootId("root-b"));

    // The reset rides the single write seam: a controlled owner is notified with the
    // default (its store, not local state, is the truth) — no dead local write.
    expect(result.current.rootId).toBe("root-b");
    expect(onSelectedIdChange).toHaveBeenCalledWith("all");
  });

  test("clamps invalid uncontrolled selections to the default", () => {
    const { result } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots: ROOTS,
        getRootId,
        getTreeRows,
        defaultSelectedId: "all",
        isSelectedIdValid: hasPseudoSelection,
      }));

    act(() => result.current.setSelectedId("missing-folder"));

    expect(result.current.selectedId).toBe("all");
    expect(result.current.selectedRow).toBeUndefined();
  });

  test("keeps a picked root independently of its option catalogue", () => {
    let roots: readonly Root[] = [ROOTS[0] as Root];
    const { result, rerender } = renderHook(() =>
      useScopedTreeExplorer<Root, TreeRow>({
        roots,
        getRootId,
        getTreeRows,
      }));

    act(() => result.current.setRootId("root-b"));
    expect(result.current.rootId).toBe("root-b");

    roots = ROOTS;
    rerender();

    expect(result.current.rootId).toBe("root-b");
    expect(result.current.treeRows).toEqual(ROWS["root-b"]);
  });
});

function getRootId(root: Root): string {
  return root.id;
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

test("a controlled root survives server search results and reports new selections to its owner", () => {
  const changed = vi.fn();
  const { result, rerender } = renderHook(({ roots, rootId }: { roots: readonly Root[]; rootId: string }) =>
    useScopedTreeExplorer({ roots, rootId, getRootId, getTreeRows, onRootIdChange: changed }),
    { initialProps: { roots: ROOTS, rootId: "root-b" } });
  rerender({ roots: ROOTS.slice(0, 1), rootId: "root-b" });
  expect(result.current.rootId).toBe("root-b");
  act(() => result.current.setRootId("root-a"));
  expect(changed).toHaveBeenCalledWith("root-a");
  expect(result.current.rootId).toBe("root-b");
  rerender({ roots: ROOTS, rootId: "root-a" });
  expect(result.current.rootId).toBe("root-a");
});


test("pending detail retains the selected scope and resolved detail overrides an earlier picker", () => {
  const { result, rerender } = renderHook(({ selectedRootId, roots }: { selectedRootId: string | null | undefined; roots: readonly Root[] }) =>
    useScopedTreeExplorer({ roots, selectedRootId, getRootId, getTreeRows }),
  { initialProps: { selectedRootId: undefined as string | null | undefined, roots: ROOTS } });
  act(() => result.current.setRootId("root-b"));
  act(() => result.current.setSelectedId("b-folder"));
  rerender({ selectedRootId: null, roots: [] });
  expect(result.current.rootId).toBe("root-b");
  expect(result.current.selectedId).toBe("b-folder");
  rerender({ selectedRootId: "root-b", roots: [] });
  expect(result.current.selectedId).toBe("b-folder");
  rerender({ selectedRootId: null, roots: ROOTS });
  rerender({ selectedRootId: "root-a", roots: ROOTS });
  expect(result.current.rootId).toBe("root-a");
  expect(result.current.selectedId).toBeUndefined();
});

test("a pending direct link defers its initial root while a list seeds only once", () => {
  const { result, rerender } = renderHook(({ selectedRootId, roots }: { selectedRootId: string | null | undefined; roots: readonly Root[] }) =>
    useScopedTreeExplorer({ roots, selectedRootId, getRootId, getTreeRows }),
  { initialProps: { selectedRootId: null as string | null | undefined, roots: ROOTS } });
  expect(result.current.rootId).toBe("");
  rerender({ selectedRootId: "root-b", roots: [] });
  expect(result.current.rootId).toBe("root-b");
  rerender({ selectedRootId: undefined, roots: ROOTS.slice(0, 1) });
  expect(result.current.rootId).toBe("root-b");
});

test("controlled empty root remains explicit and picking the same root does not reset selection", () => {
  const changed = vi.fn();
  const { result, rerender } = renderHook(({ rootId }: { rootId: string }) =>
    useScopedTreeExplorer({ roots: ROOTS, rootId, getRootId, getTreeRows, selectedId: "a-folder", onSelectedIdChange: changed }),
  { initialProps: { rootId: "root-a" } });
  act(() => result.current.setRootId("root-a"));
  expect(changed).not.toHaveBeenCalled();
  rerender({ rootId: "" });
  expect(result.current.rootId).toBe("");
  expect(result.current.treeRows).toEqual([]);
});
