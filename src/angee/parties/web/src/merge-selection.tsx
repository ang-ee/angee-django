import * as React from "react";
import { ActionFormDialog, SelectionBarAction, useRouteHref, type AllMatching, type SelectedGroup } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { OrganizationGroupSets, PeopleGroupSets, type GroupSetsDocument } from "./documents";
import { DuplicatesDialog, groupCursor, type DuplicateSetInput } from "./DuplicatesDialog";
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
 * What a parties list's "Select all" merges by: the list resource's own groups
 * read, authored here (`PeopleGroupSets`, `OrganizationGroupSets`), and the
 * fields a set may be grouped by, the text merge fields the dialog shows and
 * marks (``Party.merge_scalar_fields_of``), which that read's `key` selects.
 */
export interface MergeGroupsRead {
  document: GroupSetsDocument;
  fields: readonly string[];
}

export const PEOPLE_MERGE_GROUPS: MergeGroupsRead = {
  document: PeopleGroupSets, fields: ["display_name", "given_name", "family_name"],
};

export const ORGANIZATION_MERGE_GROUPS: MergeGroupsRead = { document: OrganizationGroupSets, fields: ["display_name"] };

/**
 * Whether "Select all" can merge what the list matched, and by which field: one
 * group level whose single dimension is one of ``groups.fields`` (no date
 * bucket, no relation, no other field), since each set is the records that share
 * a value the dialog can show and mark, read with "Repeated values only" (the
 * read's `having`), so it lists no group of one, and ordered by that field alone,
 * ascending or descending with nulls placed last, the one-field order the dialog
 * pages by key (`GroupListing`). A flat list is refused with what to do, and so is a
 * grouping without repeated values only.
 */
const ALL_MATCHING_REFUSALS = {
  records: "merge.selection.allRecords",
  grouping: "merge.selection.allGrouping",
  repeated: "merge.selection.allRepeated",
} as const;

export function allMatchingField(
  allMatching: AllMatching, groups: MergeGroupsRead,
): { field: string } | { refused: "records" | "grouping" | "repeated" } {
  if (allMatching.unit === "records") return { refused: "records" };
  // Without "Repeated values only" the read lists every group of one too: as many requests as there are names.
  if (!allMatching.read.having) return { refused: "repeated" };
  const [dimension, ...others] = allMatching.read.dimensions;
  const field = dimension?.key ?? dimension?.input;
  if (!field || others.length > 0 || dimension?.granularity || !groups.fields.includes(field)) {
    return { refused: "grouping" };
  }
  // The dialog pages by the list's own order on that field alone, in either direction (`groupCursor`, its one rule).
  if (groupCursor(allMatching.read)?.field !== field) return { refused: "grouping" };
  return { field };
}

/**
 * Three or more ticked records as duplicate sets, by the list's grouping
 * (`selectedGroups`, the list's three states): the one place that reads it.
 * `undefined`: the list is not grouped, so the server finds the sets among the
 * selection (``selection_duplicate_sets``, in the dialog).
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
 * sets `selectionSplit` reads from the list's grouping: on an ungrouped list the
 * duplicate sets the server finds among the selected records (the pairs between
 * them; three to ten with none are one set, as selected), on a grouped one a set
 * per group. Under "Select all N" (`allMatching`) the loaded ids do not count:
 * a list grouped by one text merge field (`allMatchingField`) opens the dialog on
 * every group its read matches, each group one set (`GroupListing`), and a flat
 * list or another grouping is refused with what to do. When there is no set (a
 * grouping the list cannot place, or one that holds no group of two or more with
 * a value), the verb stays in the bar, disabled, with the reason
 * (`disabledReason`). Navigation goes through the router, whose app root keeps
 * the current `?app=`.
 */
export function MergeSelectionAction({
  ids,
  selectedGroups,
  allMatching,
  groups,
  onMerged,
}: {
  ids: ReadonlySet<string>;
  selectedGroups?: readonly SelectedGroup[] | null;
  /** "Select all N": the selection is every record or group the list's read matches, not ``ids``. */
  allMatching?: AllMatching;
  /** What this list's "Select all" merges by. */
  groups: MergeGroupsRead;
  onMerged?: () => void;
}): React.ReactElement {
  const t = usePartiesT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const mergeInto = useMergeIntoAction();
  const [open, setOpen] = React.useState<"pick" | "batch" | null>(null);
  // "Select all N" merges every group the list's read matches, each group one set; the loaded ids do not count.
  const everyGroup = allMatching ? allMatchingField(allMatching, groups) : null;
  const [first, second, ...rest] = allMatching ? [] : ids;
  const single = first !== undefined && second === undefined ? first : null;
  const pair = first !== undefined && second !== undefined && rest.length === 0 ? { left: first, right: second } : null;
  const split = single || pair || allMatching ? null : selectionSplit(ids, selectedGroups);
  const sets: readonly DuplicateSetInput[] = split?.kind === "ungrouped"
    ? []
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
  // An ungrouped selection of three or more: the server finds the duplicate sets among the selected records.
  const among = split?.kind === "ungrouped" && split.ids.length > 2 ? split.ids : null;
  const reason = everyGroup
    ? "refused" in everyGroup
      ? t(ALL_MATCHING_REFUSALS[everyGroup.refused])
      : undefined
    : single || pair || among || sets.length > 0
      ? undefined
      : t(split?.kind === "unplaceable" ? "merge.selection.unplaceable" : "merge.selection.noGroups");
  return (
    <>
      <SelectionBarAction surface="brand" disabledReason={reason}
        onClick={() => {
          if (pair) void navigate({ to: routeHref("parties.merge", pair) });
          else if (single) setOpen("pick");
          else if (everyGroup || among || sets.length > 0) setOpen("batch");
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
          selection={among ?? undefined}
          allGroups={allMatching?.unit === "groups" && everyGroup && "field" in everyGroup
            ? { document: groups.document, read: allMatching.read, total: allMatching.total, field: everyGroup.field }
            : undefined}
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

/**
 * The selection verbs a parties list adds before the generic ones: Merge, told
 * the list's grouping and, under "Select all N", the read it matched, merged by
 * ``groups``. After a merge the selection clears, "Select all" with it.
 */
export function mergeSelectionActions(groups: MergeGroupsRead) {
  return function mergeSelection(
    ids: ReadonlySet<string>,
    clear?: () => void,
    collection?: { selectedGroups?: readonly SelectedGroup[] | null; allMatching?: AllMatching },
  ): React.ReactElement {
    return (
      <MergeSelectionAction ids={ids} selectedGroups={collection?.selectedGroups} allMatching={collection?.allMatching}
        groups={groups} onMerged={clear} />
    );
  };
}

/** The People list's selection verbs (`selectAllMatching`), merging by its own groups read. */
export const peopleMergeSelectionActions = mergeSelectionActions(PEOPLE_MERGE_GROUPS);

/** The Organizations list's selection verbs (`selectAllMatching`), merging by its own groups read. */
export const organizationMergeSelectionActions = mergeSelectionActions(ORGANIZATION_MERGE_GROUPS);
