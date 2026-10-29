import { useCallback, useMemo, useState } from "react";

export interface ScopedTreeExplorerOption {
  value: string;
  label: string;
}

export interface UseScopedTreeExplorerOptions<TRoot, TTreeRow extends { id: string }> {
  roots: readonly TRoot[];
  getRootId: (root: TRoot) => string;
  getTreeRows: (rootId: string) => readonly TTreeRow[];
  selectedId?: string | null;
  /**
   * Notified whenever {@link ScopedTreeExplorerController.setSelectedId} runs.
   * Pair it with a controlled `selectedId` to keep the selection in an external
   * owner (e.g. the URL search params); with the uncontrolled default it fires
   * alongside the internal state update.
   */
  onSelectedIdChange?: (selectedId: string | null) => void;
  defaultSelectedId?: string | null;
  /**
   * The selected record's resolved root. A new nonempty value overrides an
   * earlier picker choice. `null` means a record is unresolved: retain the root
   * and defer initial default selection. Omit on list routes to seed from roots.
   */
  selectedRootId?: string | null;
  /** Controlled root selection, for a picker whose options are server-searched. */
  rootId?: string;
  /** Notifies explicit root changes; resolved record roots do not rewrite routes. */
  onRootIdChange?: (rootId: string) => void;
  isSelectedIdValid?: (
    selectedId: string,
    rows: readonly TTreeRow[],
  ) => boolean;
}

export interface ScopedTreeExplorerController<TTreeRow extends { id: string }> {
  rootId: string;
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
 * @owner ScopedExplorerPane
 */
export function useScopedTreeExplorer<
  TRoot,
  TTreeRow extends { id: string },
>({
  roots,
  getRootId,
  getTreeRows,
  selectedId,
  onSelectedIdChange,
  defaultSelectedId = null,
  selectedRootId,
  rootId: controlledRootId,
  onRootIdChange,
  isSelectedIdValid,
}: UseScopedTreeExplorerOptions<TRoot, TTreeRow>): ScopedTreeExplorerController<TTreeRow> {
  const [selection, setSelection] = useState({
    rootId: selectedRootId ?? null,
    selectedRootId,
  });
  const [localSelectedId, setLocalSelectedId] = useState<string | null>(
    defaultSelectedId,
  );

  // Observe resolved record roots during render, before publishing descendants.
  // A pending read and a changed option catalogue never erase a chosen scope.
  const nextRootId = selectedRootId && selectedRootId !== selection.selectedRootId
    ? selectedRootId
    : selection.rootId ?? (selectedRootId === undefined && roots[0] ? getRootId(roots[0]) : null);
  if (selection.selectedRootId !== selectedRootId || selection.rootId !== nextRootId) {
    setSelection({ rootId: nextRootId, selectedRootId });
  }
  const rootId = controlledRootId ?? nextRootId ?? "";
  const [selectionRootId, setSelectionRootId] = useState(rootId);
  if (selectionRootId !== rootId) {
    setSelectionRootId(rootId);
    if (selectedId === undefined) setLocalSelectedId(defaultSelectedId);
  }
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
      if (nextRootId === rootId) return;
      if (controlledRootId === undefined) setSelection({ rootId: nextRootId, selectedRootId });
      onRootIdChange?.(nextRootId);
      // Reset the selection through the same ownership split as the write seam
      // below: uncontrolled updates local state; controlled notifies its external
      // owner (local state is dead there — the store, not this hook, is the truth).
      if (ownsSelection) setLocalSelectedId(defaultSelectedId);
      else onSelectedIdChange?.(defaultSelectedId);
    },
    [controlledRootId, defaultSelectedId, onRootIdChange, onSelectedIdChange, ownsSelection, rootId, selectedRootId],
  );
  // The single selection-write seam: a controlled owner (selectedId passed)
  // only gets notified — its store, not local state, is the source of truth;
  // the uncontrolled default updates local state and notifies any observer.
  const handleSetSelectedId = useCallback(
    (nextSelectedId: string | null) => {
      if (ownsSelection) setLocalSelectedId(nextSelectedId);
      onSelectedIdChange?.(nextSelectedId);
    },
    [onSelectedIdChange, ownsSelection],
  );

  return useMemo(
    () => ({
      rootId,
      setRootId: handleSetRootId,
      treeRows,
      selectedId: effectiveSelectedId,
      selectedRow,
      setSelectedId: handleSetSelectedId,
    }),
    [
      rootId,
      handleSetRootId,
      treeRows,
      effectiveSelectedId,
      selectedRow,
      handleSetSelectedId,
    ],
  );
}
