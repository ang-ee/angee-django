import * as React from "react";
import { ActionFormDialog, SelectionBarAction, useRouteHref, type SelectedGroup } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { DuplicatesDialog, type DuplicateSetInput } from "./DuplicatesDialog";
import { usePartiesT } from "./i18n";
import { useMergeIntoAction } from "./merge-into";

/**
 * The most sets one batch merge takes: a copy of the server's
 * ``PartyManager.BATCH_SETS``, which refuses a larger batch. No read this
 * fragment receives carries it; its smallest owner would be one more field on
 * the review counts read (``same_as_pair_counts``), not a query of its own.
 */
const BATCH_SETS = 50;

/**
 * Three or more ticked records as duplicate sets, by the list's grouping
 * (`selectedGroups`, the list's three states): the one place that reads it.
 * `undefined`: the list is not grouped, so the whole selection is one set.
 * `null`: the list is grouped but a ticked row cannot be placed (or the surface
 * places no rows), so there is no batch at all. An array: one set per innermost
 * group with two or more ticked rows, carrying its levels (parents, then the
 * group) so the dialog heads it and marks a member that no longer matches them;
 * a group of one is left out and counted, and a group without a value (`null` or
 * blank) is never a set, since everyone without that value sits in it; never one
 * set across groups. The grouping is the list's as of the selection, never
 * evidence: the dialog reads each member fresh and the server judges each set by
 * its member ids alone.
 */
export type SelectionSplit =
  | { kind: "ungrouped"; ids: readonly string[] }
  | { kind: "grouped"; sets: readonly DuplicateSetInput[]; unpaired: number; blank: number }
  | { kind: "unplaceable" };

export function selectionSplit(
  ids: ReadonlySet<string>,
  selectedGroups: readonly SelectedGroup[] | null | undefined,
): SelectionSplit {
  if (selectedGroups === undefined) return { kind: "ungrouped", ids: [...ids] };
  if (selectedGroups === null) return { kind: "unplaceable" };
  const sets: DuplicateSetInput[] = [];
  let unpaired = 0;
  let blank = 0;
  for (const group of selectedGroups) {
    // A whitespace-only key counts as blank here, though the server would group it as a value:
    // that errs safe, leaving those records out of a batch rather than merging them as one.
    if (group.key === null || group.key.trim() === "") {
      blank += group.ids.length;
    } else if (group.ids.length < 2) {
      unpaired += group.ids.length;
    } else {
      const levels = [...group.parents, group];
      sets.push({
        members: group.ids,
        heading: levels.map((level) => level.label).join(" › "),
        grouping: levels.map(({ field, key }) => ({ field, key })),
      });
    }
  }
  return { kind: "grouped", sets, unpaired, blank };
}

/**
 * A parties list's Merge in its selection bar. The count decides first: one
 * ticked opens "Merge into…" for it (`useMergeIntoAction`, the person form's
 * verb), where the person chooses the other record; two open the compare page for
 * the pair (`parties.merge`), which owns the merge and its choices, whatever the
 * grouping. Three or more open the batch dialog (`DuplicatesDialog`) with the
 * sets `selectionSplit` reads from the list's grouping: one for an ungrouped
 * list, one per group of a grouped one. When there is no set (a grouping the list
 * cannot place, or one that holds no group of two or more with a value), the verb
 * stays in the bar, disabled, with the reason (`disabledReason`). Navigation goes
 * through the router, whose app root keeps the current `?app=`.
 */
export function MergeSelectionAction({
  ids,
  selectedGroups,
  onMerged,
}: {
  ids: ReadonlySet<string>;
  selectedGroups?: readonly SelectedGroup[] | null;
  onMerged?: () => void;
}): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const mergeInto = useMergeIntoAction();
  const [open, setOpen] = React.useState<"pick" | "batch" | null>(null);
  const [first, second, ...rest] = ids;
  const single = first !== undefined && second === undefined ? first : null;
  const pair = first !== undefined && second !== undefined && rest.length === 0 ? { left: first, right: second } : null;
  const split = single || pair ? null : selectionSplit(ids, selectedGroups);
  const sets: readonly DuplicateSetInput[] = split?.kind === "ungrouped"
    ? (split.ids.length > 2 ? [{ members: split.ids }] : [])
    : split?.kind === "grouped" ? split.sets.slice(0, BATCH_SETS) : [];
  const notes = split?.kind === "grouped"
    ? [
      ...(split.unpaired ? [t("duplicates.unpaired", { count: split.unpaired })] : []),
      ...(split.blank ? [t("duplicates.blank", { count: split.blank })] : []),
      // A batch takes at most BATCH_SETS sets: the first ones in the list's order. The dialog says how many are left;
      // after a merge the list's selection clears (the shared selection offers only `clear`), so the rest is re-selected.
      ...(split.sets.length > BATCH_SETS ? [t("duplicates.overCap", { count: BATCH_SETS, total: split.sets.length })] : []),
    ]
    : [];
  const reason = single || pair || sets.length > 0
    ? undefined
    : t(split?.kind === "unplaceable" ? "merge.selection.unplaceable" : "merge.selection.noGroups");
  return (
    <>
      <SelectionBarAction surface="brand" disabledReason={reason}
        onClick={() => {
          if (pair) void navigate({ to: routeHref("parties.merge", pair) });
          else if (single) setOpen("pick");
          else if (sets.length > 0) setOpen("batch");
        }}>
        {t("merge.selection.action")}
      </SelectionBarAction>
      {open === "pick" && single ? (
        <ActionFormDialog
          action={mergeInto}
          context={{ record: { id: single }, selectedIds: [single] }}
          open
          onOpenChange={(next) => { if (!next) setOpen(null); }}
        />
      ) : null}
      {open === "batch" ? (
        <DuplicatesDialog
          sets={sets}
          notes={notes}
          onClose={(merged) => {
            setOpen(null);
            if (merged) onMerged?.();
          }}
        />
      ) : null}
    </>
  );
}

/** The selection verbs a parties list adds before the generic ones: Merge, told the list's grouping. */
export function mergeSelectionActions(
  ids: ReadonlySet<string>,
  clear?: () => void,
  collection?: { selectedGroups?: readonly SelectedGroup[] | null },
): React.ReactElement {
  return <MergeSelectionAction ids={ids} selectedGroups={collection?.selectedGroups} onMerged={clear} />;
}
