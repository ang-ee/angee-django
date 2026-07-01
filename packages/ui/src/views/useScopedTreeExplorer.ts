import { useCallback, useMemo, useState } from "react";

export interface ScopedTreeExplorerOption {
  value: string;
  label: string;
}

export interface UseScopedTreeExplorerOptions<TRoot, TTreeRow extends { id: string }> {
  roots: readonly TRoot[];
  getRootId: (root: TRoot) => string;
  getRootLabel: (root: TRoot) => string;
  getTreeRows: (rootId: string) => readonly TTreeRow[];
  selectedId?: string | null;
  defaultSelectedId?: string | null;
  selectedRootId?: string | null;
  isSelectedIdValid?: (
    selectedId: string,
    rows: readonly TTreeRow[],
  ) => boolean;
}

export interface ScopedTreeExplorerController<
  TRoot,
  TTreeRow extends { id: string },
> {
  root: TRoot | undefined;
  rootId: string;
  rootOptions: readonly ScopedTreeExplorerOption[];
  setRootId: (rootId: string) => void;
  treeRows: readonly TTreeRow[];
  selectedId: string | undefined;
  selectedRow: TTreeRow | undefined;
  setSelectedId: (selectedId: string | null) => void;
}

/**
 * Shared state owner for explorer pages with a root picker and scoped tree. The
 * hook owns root pinning and tree-selection clamping; addon pages still own
 * route state, row projection, DnD policy, and domain actions.
 */
export function useScopedTreeExplorer<
  TRoot,
  TTreeRow extends { id: string },
>({
  roots,
  getRootId,
  getRootLabel,
  getTreeRows,
  selectedId,
  defaultSelectedId = null,
  selectedRootId = null,
  isSelectedIdValid,
}: UseScopedTreeExplorerOptions<TRoot, TTreeRow>): ScopedTreeExplorerController<
  TRoot,
  TTreeRow
> {
  const [pinnedRootId, setPinnedRootId] = useState<string | null>(null);
  const [localSelectedId, setLocalSelectedId] = useState<string | null>(
    defaultSelectedId,
  );

  const rootOptions = useMemo(
    () =>
      roots.map((root) => ({
        value: getRootId(root),
        label: getRootLabel(root),
      })),
    [roots, getRootId, getRootLabel],
  );
  const rootIds = useMemo(
    () => new Set(rootOptions.map((option) => option.value)),
    [rootOptions],
  );

  const hintedRootId =
    selectedRootId && rootIds.has(selectedRootId) ? selectedRootId : null;
  const rootId =
    pinnedRootId && rootIds.has(pinnedRootId)
      ? pinnedRootId
      : hintedRootId ?? rootOptions[0]?.value ?? "";
  const root = useMemo(
    () => roots.find((candidate) => getRootId(candidate) === rootId),
    [roots, getRootId, rootId],
  );
  const treeRows = useMemo(
    () => (rootId ? getTreeRows(rootId) : []),
    [getTreeRows, rootId],
  );

  const ownsSelection = selectedId === undefined;
  const requestedSelectedId = ownsSelection ? localSelectedId : selectedId;
  const validSelectedId = useCallback(
    (candidate: string) =>
      isSelectedIdValid
        ? isSelectedIdValid(candidate, treeRows)
        : treeRows.some((row) => row.id === candidate),
    [isSelectedIdValid, treeRows],
  );
  const fallbackSelectedId =
    defaultSelectedId && validSelectedId(defaultSelectedId)
      ? defaultSelectedId
      : undefined;
  const effectiveSelectedId =
    requestedSelectedId && validSelectedId(requestedSelectedId)
      ? requestedSelectedId
      : fallbackSelectedId;
  const selectedRow = effectiveSelectedId
    ? treeRows.find((row) => row.id === effectiveSelectedId)
    : undefined;

  const handleSetRootId = useCallback(
    (nextRootId: string) => {
      setPinnedRootId(nextRootId);
      setLocalSelectedId(defaultSelectedId);
    },
    [defaultSelectedId],
  );

  return {
    root,
    rootId,
    rootOptions,
    setRootId: handleSetRootId,
    treeRows,
    selectedId: effectiveSelectedId,
    selectedRow,
    setSelectedId: setLocalSelectedId,
  };
}
