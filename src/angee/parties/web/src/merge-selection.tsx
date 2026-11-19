import * as React from "react";
import { ActionFormDialog, SelectionBarAction, Tooltip, useRouteHref } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { DuplicatesDialog, type DuplicateSetInput } from "./DuplicatesDialog";
import { usePartiesT } from "./i18n";
import { useMergeIntoAction } from "./merge-into";

/** The most sets one batch merge takes (the server's ``PartyManager.BATCH_SETS``). */
const BATCH_SETS = 50;

/** One level of a group's path: the field the list grouped by and the group's value as text. */
interface SelectedGroupLevel {
  field: string;
  key: string | null;
  label: string;
}

/**
 * One group of the list's active grouping that holds selected rows. A narrow local
 * copy of the optional `selectedGroups` member the list's group-by adds to a bulk
 * action's `collection`; remove it for the shared type once that member is in
 * `@angee/ui` (`resource-view-types.ts`, the `bulkActions` collection).
 */
interface SelectedGroup extends SelectedGroupLevel {
  parents: readonly SelectedGroupLevel[];
  ids: readonly string[];
}

/**
 * What the selection is, as duplicate sets: the one place that reads the list's
 * grouping. `undefined` grouping: the list is not grouped, so the whole selection
 * is one set. `null`: the list is grouped but a selected row is not placed (a
 * select-all across pages, any board), so there is no batch at all. An array: one
 * set per group with two or more selected rows, carrying the group's levels so
 * the dialog can mark a member that no longer matches them; a group of one is
 * counted, and a group without a value (`null` or blank) is never a set, since
 * everyone without that value sits in it. The grouping is the list's as of the
 * selection, never evidence: the dialog reads each member fresh and the server
 * judges each set by its member ids alone.
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
 * A parties list's Merge in its selection bar, by how many records are ticked.
 * One opens "Merge into…" for it (`useMergeIntoAction`, the person form's verb):
 * the person chooses the other record and the compare page opens. Two open the
 * compare page for the pair (`parties.merge`), which owns the merge and its
 * choices. Three or more open the batch dialog (`DuplicatesDialog`): one set for
 * an ungrouped list, one per group for a grouped one. The verb is disabled, with
 * its reason as the tooltip, only when it cannot run: three or more whose groups
 * the list cannot place, or a grouping that holds no set. Navigation goes through
 * the router, whose app root keeps the current `?app=`.
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
  const mergeInto = useMergeIntoAction();
  const [open, setOpen] = React.useState<"pick" | "batch" | null>(null);
  const [first, second, ...rest] = ids;
  const single = first !== undefined && second === undefined ? first : null;
  const pair = first !== undefined && second !== undefined && rest.length === 0 ? { left: first, right: second } : null;
  const split = single || pair ? null : selectionSplit(ids, collection);
  const sets: readonly DuplicateSetInput[] = split?.kind === "ungrouped"
    ? (split.ids.length > 2 ? [{ members: split.ids }] : [])
    : split?.kind === "grouped" ? split.sets.slice(0, BATCH_SETS) : [];
  const notes = split?.kind === "grouped"
    ? [
      ...(split.unpaired ? [t("duplicates.unpaired", { count: split.unpaired })] : []),
      ...(split.blank ? [t("duplicates.blank", { count: split.blank })] : []),
      ...(split.sets.length > BATCH_SETS ? [t("duplicates.overCap", { count: BATCH_SETS })] : []),
    ]
    : [];
  const reason = single || pair || sets.length > 0
    ? undefined
    : t(split?.kind === "unplaceable" ? "merge.selection.unplaceable" : "merge.selection.noGroups");
  return (
    <>
      {/* A disabled reason is the verb's tooltip; the verb stays focusable so keyboard users reach it. */}
      <Tooltip label={reason} side="bottom">
        <SelectionBarAction surface="brand" disabled={Boolean(reason)} focusableWhenDisabled={Boolean(reason)}
          onClick={() => {
            if (pair) void navigate({ to: routeHref("parties.merge", pair) });
            else if (single) setOpen("pick");
            else if (sets.length > 0) setOpen("batch");
          }}>
          {t("merge.selection.action")}
        </SelectionBarAction>
      </Tooltip>
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

/** The selection verbs a parties list adds before the generic ones: Merge. */
export function mergeSelectionActions(
  ids: ReadonlySet<string>,
  clear?: () => void,
  collection?: object,
): React.ReactElement {
  return <MergeSelectionAction ids={ids} collection={collection} onMerged={clear} />;
}
