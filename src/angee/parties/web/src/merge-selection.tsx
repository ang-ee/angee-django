import * as React from "react";
import { SelectionBarAction, Tooltip, useRouteHref } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { DuplicatesDialog, type DuplicateSetInput } from "./DuplicatesDialog";
import { usePartiesT } from "./i18n";

/** The most sets one batch merge takes (the server's ``PartyManager.BATCH_SETS``). */
const BATCH_SETS = 50;

/**
 * One group of the list's active grouping that holds selected rows. A narrow local
 * copy of the optional `selectedGroups` member the list's group-by adds to a bulk
 * action's `collection`; remove it for the shared type once that member is in
 * `@angee/ui` (`resource-view-types.ts`, the `bulkActions` collection).
 */
interface SelectedGroup {
  field: string;
  key: string | null;
  label: string;
  parents: readonly { field: string; key: string | null; label: string }[];
  ids: readonly string[];
}

/**
 * What the selection is, as duplicate sets: the one place that reads the list's
 * grouping. `undefined` grouping: the list is not grouped, so the whole selection
 * is one set. `null`: the list is grouped but a selected row is not placed (a
 * select-all across pages), so there is no batch at all. An array: one set per
 * group with two or more selected rows; a group of one is counted, and a group
 * without a value (`null` or blank) is never a set, since everyone without that
 * value sits in it.
 */
export type SelectionSplit =
  | { kind: "ungrouped"; ids: readonly string[] }
  | { kind: "grouped"; sets: readonly DuplicateSetInput[]; unpaired: number; blank: number }
  | { kind: "unplaceable" };

export function selectionSplit(ids: ReadonlySet<string>, collection?: object): SelectionSplit {
  const groups = (collection as { selectedGroups?: readonly SelectedGroup[] | null } | undefined)?.selectedGroups;
  if (groups === undefined) return { kind: "ungrouped", ids: [...ids] };
  if (groups === null) return { kind: "unplaceable" };
  const sets: DuplicateSetInput[] = [];
  let unpaired = 0;
  let blank = 0;
  for (const group of groups) {
    if (group.key === null || group.key.trim() === "") {
      blank += group.ids.length;
    } else if (group.ids.length < 2) {
      unpaired += group.ids.length;
    } else {
      sets.push({ members: group.ids, heading: [...group.parents.map((parent) => parent.label), group.label].join(" › ") });
    }
  }
  return { kind: "grouped", sets, unpaired, blank };
}

/**
 * A parties list's Merge in its selection bar. Two selected records of an
 * ungrouped list open the compare page for that pair (`parties.merge`), which
 * owns the merge and its choices. Three or more, or a grouped selection, open the
 * batch dialog (`DuplicatesDialog`) with one set per group. Otherwise the verb
 * stays in the bar, disabled, with its reason as the tooltip. Navigation goes
 * through the router, whose app root keeps the current `?app=`.
 */
export function MergeSelectionAction({
  ids,
  collection,
  onMerged,
}: {
  ids: ReadonlySet<string>;
  collection?: object;
  onMerged?: () => void;
}): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const [open, setOpen] = React.useState(false);
  const split = selectionSplit(ids, collection);
  const pair = split.kind === "ungrouped" && split.ids.length === 2 ? { left: split.ids[0]!, right: split.ids[1]! } : null;
  const sets: readonly DuplicateSetInput[] = split.kind === "ungrouped"
    ? (split.ids.length > 2 ? [{ members: split.ids }] : [])
    : split.kind === "grouped" ? split.sets.slice(0, BATCH_SETS) : [];
  const notes = split.kind === "grouped"
    ? [
      ...(split.unpaired ? [t("duplicates.unpaired", { count: split.unpaired })] : []),
      ...(split.blank ? [t("duplicates.blank", { count: split.blank })] : []),
      ...(split.sets.length > BATCH_SETS ? [t("duplicates.overCap", { count: BATCH_SETS })] : []),
    ]
    : [];
  const reason = pair || sets.length > 0
    ? undefined
    : t(split.kind === "unplaceable" ? "merge.selection.unplaceable"
      : split.kind === "grouped" ? "merge.selection.noGroups" : "merge.selection.tooFew");
  return (
    <>
      {/* A disabled reason is the verb's tooltip; the verb stays focusable so keyboard users reach it. */}
      <Tooltip label={reason} side="bottom">
        <SelectionBarAction surface="brand" disabled={Boolean(reason)} focusableWhenDisabled={Boolean(reason)}
          onClick={() => {
            if (pair) void navigate({ to: routeHref("parties.merge", pair) });
            else if (sets.length > 0) setOpen(true);
          }}>
          {t("merge.selection.action")}
        </SelectionBarAction>
      </Tooltip>
      {open ? (
        <DuplicatesDialog
          sets={sets}
          notes={notes}
          onClose={(merged) => {
            setOpen(false);
            if (merged) onMerged?.();
          }}
        />
      ) : null}
    </>
  );
}

/** The selection verbs a parties list adds before the generic ones: Merge. */
export function mergeSelectionActions(
  ids: ReadonlySet<string>,
  clear?: () => void,
  collection?: object,
): React.ReactElement {
  return <MergeSelectionAction ids={ids} collection={collection} onMerged={clear} />;
}
