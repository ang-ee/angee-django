import { createContext, useCallback, useContext, useMemo } from "react";
import type { RowSelectionState, Updater } from "@tanstack/react-table";
import type { Row } from "@angee/metadata";
import type { SelectionPlace } from "./resource-view-types";
import type { ResourceListResult } from "./surface/types";

/**
 * "Select all N": the key of the list read whose every match the person chose, the
 * pager's unit it counts, the total it last settled with, and the list surface that
 * chose it, the only one whose leaving ends it.
 */
export interface AllMatchingMode {
  readKey: string;
  unit: ResourceListResult["pageUnit"];
  total: number;
  surface: string;
}

/** One scope's selection as its owner (`ResourceViewProvider`) holds it. */
export interface SelectionRead {
  rowSelection: RowSelectionState;
  rows: Readonly<Record<string, Row>>;
  /** The place a grouped surface showed each row in when selected; absent for a row selected without one. */
  places: Readonly<Record<string, SelectionPlace>>;
  /** "Select all N", or null when the selection is its ids. The ids stay the selected ones while it is on. */
  allMatching: AllMatchingMode | null;
}

/**
 * What a list surface shows selected under "Select all N" over its read: its loaded
 * rows, with the row and the place it shows each one in.
 */
export interface ShownSelection {
  readKey: string;
  ids: readonly string[];
  observed: (id: string) => Row | undefined;
  placed?: (id: string) => SelectionPlace | undefined;
}

export interface SelectionOwner {
  read: (scope: string) => SelectionRead;
  /**
   * Any write leaves "Select all N". One from a surface that shows it (`shown`) starts
   * from what that surface shows selected, so the selection becomes those rows with the
   * write applied and an id selected before that does not show is dropped.
   */
  update: (
    scope: string,
    updater: Updater<RowSelectionState>,
    observed?: (id: string) => Row | undefined,
    placed?: (id: string) => SelectionPlace | undefined,
    shown?: ShownSelection,
  ) => void;
  enter: (scope: string) => void;
  selectAllMatching: (scope: string, mode: AllMatchingMode) => void;
  updateAllMatching: (scope: string, readKey: string, total: number) => void;
  leaveAllMatching: (scope: string, surface: string) => void;
}

export const EMPTY_SELECTION: SelectionRead = { rowSelection: {}, rows: {}, places: {}, allMatching: null };
export const SelectionOwnerContext = createContext<SelectionOwner | null>(null);
/** The selection scope of the nearest inherited view; the owner's own collection is "". */
export const SelectionScopeContext = createContext("");

/** The list surfaces' own channel to their scope's selection (`useSelectionPlaces`). */
export interface SelectionPlaces {
  /** The place a grouped surface showed each selected row in when it was selected. */
  places: Readonly<Record<string, SelectionPlace>>;
  /** `setRowSelection`, also capturing where `placed` shows each selected row it places, newly or already selected. */
  setRowSelection: (
    updater: Updater<RowSelectionState>,
    observed?: (id: string) => Row | undefined,
    placed?: (id: string) => SelectionPlace | undefined,
  ) => void;
  /** `toggleSelectedId`, also capturing where the row shows. */
  toggleSelectedId: (id: string, selected?: boolean, row?: Row, place?: SelectionPlace) => void;
  /** "Select all N" in this scope, any surface's, or null. */
  allMatching: AllMatchingMode | null;
  /** The person's choice: select every match of a read, keeping the selected ids; nothing without a selection. */
  selectAllMatching: (mode: AllMatchingMode) => void;
  /** The total the mode chosen over this read settled with; never enters or leaves it. */
  updateAllMatching: (readKey: string, total: number) => void;
  /** This surface reads otherwise or no longer shows: the mode it chose ends, another surface's stays. */
  leaveAllMatching: (surface: string) => void;
}

/** One scope's selection and its writes, a surface's carrying what it shows selected. */
export function useScopedSelection(scope: string, owner: SelectionOwner, shown?: ShownSelection) {
  const read = owner.read(scope);
  const setRowSelection = useCallback<SelectionPlaces["setRowSelection"]>(
    (updater, observed, placed) => owner.update(scope, updater, observed, placed, shown), [owner.update, scope, shown],
  );
  const toggleSelectedId = useCallback<SelectionPlaces["toggleSelectedId"]>(
    (id, selected, row, place) => setRowSelection((current) => ({
      ...current, [id]: selected ?? !current[id],
    }), row === undefined ? undefined : (selectedId) => selectedId === id ? row : undefined,
    place === undefined ? undefined : (selectedId) => selectedId === id ? place : undefined),
    [setRowSelection],
  );
  const selectAllMatching = useCallback<SelectionPlaces["selectAllMatching"]>(
    (mode) => owner.selectAllMatching(scope, mode), [owner.selectAllMatching, scope],
  );
  const updateAllMatching = useCallback<SelectionPlaces["updateAllMatching"]>(
    (readKey, total) => owner.updateAllMatching(scope, readKey, total), [owner.updateAllMatching, scope],
  );
  const leaveAllMatching = useCallback<SelectionPlaces["leaveAllMatching"]>(
    (surface) => owner.leaveAllMatching(scope, surface), [owner.leaveAllMatching, scope],
  );
  return { ...read, setRowSelection, toggleSelectedId, selectAllMatching, updateAllMatching, leaveAllMatching };
}

/**
 * The list's channel to the selection of the scope it renders in: the places captured
 * with the snapshots, the writes that capture them, and the "Select all N" mode. Not
 * part of the package's API: a page reads the split only through `bulkActions` →
 * `selectedGroups`, which the list computes and checks against what it shows, and the
 * mode through `bulkActions` → `allMatching`. `shown` is what the calling surface
 * shows selected under the mode over its read, which its writes start from.
 * @owner ResourceViewProvider
 */
export function useSelectionPlaces(shown?: ShownSelection): SelectionPlaces {
  const owner = useContext(SelectionOwnerContext);
  const scope = useContext(SelectionScopeContext);
  if (!owner) throw new Error("A grouped list requires a resource view.");
  const {
    places, setRowSelection, toggleSelectedId, allMatching, selectAllMatching, updateAllMatching, leaveAllMatching,
  } = useScopedSelection(scope, owner, shown);
  return useMemo(() => ({
    places, setRowSelection, toggleSelectedId, allMatching, selectAllMatching, updateAllMatching, leaveAllMatching,
  }), [places, setRowSelection, toggleSelectedId, allMatching, selectAllMatching, updateAllMatching, leaveAllMatching]);
}
