import * as React from "react";
import { useResourceInvalidates } from "@angee/metadata";
import {
  groupByVariables, useAuthoredMutation, useAuthoredQuery, useInvalidateAuthoredModels, type AuthoredDocument,
  type GroupByVariables,
} from "@angee/refine";
import { useInvalidate } from "@refinedev/core";
import {
  Alert, Button, Checkbox, Chip, Dialog, ErrorBanner, LoadingPanel, NavLink, Pager, RowsListView, defaultWidgets,
  formatNumber, optionLabel, useEnumOptions, useRouteHref, type AllMatchingGroups, type ListColumn,
} from "@angee/ui";

import {
  CertainSameAsPairs,
  DuplicateSetPreviews,
  MergeDuplicates,
  PARTY_MERGE_INVALIDATES,
  PARTY_MERGE_RESOURCES,
  SelectionDuplicateSets,
  type CertainSameAsPair,
  type DuplicateSetMember,
  type GroupSetsBucket,
  type GroupSetsDocument,
  type DuplicateSetOutcome,
  type DuplicateSetPreview,
} from "./documents";
import { usePartiesT } from "./i18n";

const DateTimeRead = defaultWidgets.datetime.read;

const NO_SETS: readonly DuplicateSetInput[] = [];

/**
 * How many sets one read lists or previews: the server's bound on one request
 * (``PartyManager.BATCH_SETS``). A longer list loads one page after another.
 */
export const SETS_PAGE = 50;

/**
 * The most sets one confirm lists and runs. Review's "every certain pair" stops
 * loading there and says how to reach the rest: run again.
 */
export const CONFIRM_SETS = 1000;

/** How many sets the dialog renders at a time; the shared pager pages the rest. */
const SHOWN_SETS = 50;

/**
 * How many sets one merge request carries. A run sends the confirmed sets this
 * many at a time, in order, so no request lasts long and the person sees each
 * chunk's outcomes as it answers; each set still merges in its own transaction.
 */
export const MERGE_CHUNK = 5;

/**
 * One set of records a person says are one: its member ids, and what its list
 * grouping said of it at selection, the heading and each level's field and value.
 * Only the ids are sent; the grouping marks a member that no longer matches it.
 */
export interface DuplicateSetInput {
  members: readonly string[];
  heading?: string;
  grouping?: readonly { field: string; key: string | null }[];
}

/**
 * Every group a list's "Select all N" matched (`allMatching.read`, one group
 * level): the dialog pages the list resource's own groups read with it, each
 * group of two or more records with a value one set grouped by ``field``.
 */
export interface GroupListing {
  /** The list resource's own groups read (`MergeGroupsRead.document`). */
  document: GroupSetsDocument;
  /** The list's read, its order included, forwarded as it is through `groupByVariables`. */
  read: AllMatchingGroups["read"];
  /** How many groups the list matched (`allMatching.total`). */
  total: number;
  /** The text merge field the groups share: each set's grouping. */
  field: string;
}

/**
 * Each group's first rows: one more than the server's member cap
 * (``PartyManager.SET_MEMBERS``, ten), so a larger group reaches the preview
 * whole enough to be refused there, as any set over the cap is.
 */
const GROUP_ROWS = 11;

/** What one page of a paged source answers: certain pairs, or a list's groups. */
type ListedData = { certain_same_as_pairs: readonly CertainSameAsPair[] } | { groups: readonly GroupSetsBucket[] };

/** One page a paged source listed: what it read, how many items it held, the sets they gave and who was left out. */
interface ListedPage {
  request: string;
  items: number;
  sets: readonly DuplicateSetInput[];
  /** What the next page continues after: the last certain pair, or the last listed group's key. */
  after: string | null;
  /** Nothing can follow this page, full or not: a group page ending on a key no `_gt` cursor can step past. */
  ended: boolean;
  /** Records in a group of one, and in a group without a value: never a set. */
  unpaired: number;
  blank: number;
}

/**
 * The field the list's groups are paged by: its own order's one field, the one rule of which orders a batch can
 * page by key. The list orders a level's groups by the group's own key or keys, ascending, nulls last
 * (`GroupAxis.groupBy()`); a one-field order is a plain column's, the dimensions a batch merges by. Any other order
 * has no cursor: more than one field, a descending one, or nulls first, which would put a key no `_gt` can step past
 * ahead of the others. The reader refuses those (`allMatchingField`).
 */
export function cursorField(read: AllMatchingGroups["read"]): string | null {
  const [order, ...others] = read.orderBy ?? [];
  if (!order || others.length > 0 || order.direction === "DESC") return null;
  return (order.nulls ?? "LAST") === "LAST" ? order.field : null;
}

/**
 * The wire variables of one page of the list's groups: the list's read as it is, its order included, narrowed to
 * the groups past the last listed key, with each group's rows. Paging by key, not by offset, a group added or removed
 * while the list is read neither repeats nor skips another. The narrowing names the key's own field, as a plain
 * column's key does in the resource's filter. It never steps over a null key: the order puts nulls last, and under
 * "Select all" the read has a `having` and a repeated level's `where` keeps no group without a value.
 */
function groupSetsVariables(groups: GroupListing, after: string | null): GroupByVariables & { rows_limit: number } {
  const field = cursorField(groups.read);
  const where = after === null || field === null
    ? groups.read.where
    : { _and: [...(groups.read.where ? [groups.read.where] : []), { [field]: { _gt: after } }] };
  return { ...groupByVariables({ ...groups.read, where, pageSize: SETS_PAGE }), rows_limit: GROUP_ROWS };
}

/** One answered page as sets: each certain pair, or each group of two or more records with a value. */
function listedPage(
  data: ListedData, request: string, groups: GroupListing | undefined, t: ReturnType<typeof usePartiesT>,
): ListedPage {
  const field = groups?.field;
  if ("certain_same_as_pairs" in data) {
    const pairs = data.certain_same_as_pairs;
    return {
      request, items: pairs.length, after: pairs.at(-1)?.id ?? null, ended: false, unpaired: 0, blank: 0,
      sets: pairs.map((pair) => ({
        members: [pair.left.id, pair.right.id],
        heading: t("review.sameAs.certainHeading", {
          left: pair.left.display_name, right: pair.right.display_name, address: pair.normalized_value,
        }),
      })),
    };
  }
  const sets: DuplicateSetInput[] = [];
  let unpaired = 0;
  let blank = 0;
  for (const group of data.groups) {
    const value = field ? (group.key as Record<string, unknown>)[field] : null;
    const key = typeof value === "string" ? value : null;
    // A blank-looking key is left out as blank, as a ticked group's is: everyone without a value sits in it.
    if (key === null || key.trim() === "") blank += group.aggregate.count;
    else if (group.aggregate.count < 2) unpaired += group.aggregate.count;
    else sets.push({ members: group.rows.map((row) => row.id), heading: key, grouping: [{ field: field!, key }] });
  }
  const cursor = groups ? cursorField(groups.read) : null;
  const last = cursor ? (data.groups.at(-1)?.key as Record<string, unknown> | undefined)?.[cursor] : undefined;
  // Fail closed: a page whose last key is null (nulls sort last, so nothing follows it) or not text (no `_gt` cursor
  // here) ends the listing, as a page without a cursor does; it never restarts from the first page.
  const after = typeof last === "string" ? last : null;
  return { request, items: data.groups.length, after, ended: after === null, unpaired, blank, sets };
}

/** One confirmed run: the sets as confirmed, their outcomes as the chunks answer, and how it ended. */
interface MergeRun {
  shown: readonly DuplicateSetPreview[];
  queue: readonly DuplicateSetPreview[];
  outcomes: ReadonlyMap<DuplicateSetPreview, DuplicateSetOutcome>;
  status: "running" | "done" | "stopped" | "failed";
}

export interface DuplicatesDialogProps {
  sets?: readonly DuplicateSetInput[];
  /**
   * Records selected on an ungrouped list: the server finds the duplicate sets
   * among them (``selection_duplicate_sets``) and the dialog lists those, said
   * what was left out; ``sets`` is then unused.
   */
  selection?: readonly string[];
  /**
   * Every certain pair awaiting the person's review: the dialog lists them page
   * by page (``certain_same_as_pairs(after:)``), at most :data:`CONFIRM_SETS`,
   * saying "loaded N of M" with ``total`` as the Review page shows it; ``sets``
   * is then unused and each set is re-checked as certain.
   */
  allCertain?: { total: string };
  /** Every group a list's "Select all N" matched (`GroupListing`); ``sets`` is then unused. */
  allGroups?: GroupListing;
  /** The Review page's certain pairs: each set is re-checked as still certain when merged. */
  certain?: boolean;
  /**
   * Lines about the sets around these ones: how the next batch is reached (a
   * selection over the batch cap) and what a list selection left out (records
   * with no one to merge with, groups without a value).
   */
  notes?: readonly string[];
  onClose: (merged: boolean) => void;
}

/**
 * The batch merge's one dialog. It lists each set with what tells its records
 * apart (addresses, sources, dates), which record survives and why, and the fields
 * they disagree on with the value that will show. Each set that may merge has its
 * own Include toggle, off until the person ticks it when the set carries a
 * warning (its records disagree, or one no longer matches its group), and off
 * again whenever the set changes from the state the person saw or ticked it in
 * (a refreshed preview, a refused set that becomes mergeable), so nothing is
 * included in a state the person did not see. One confirm merges exactly the
 * included sets, sent :data:`MERGE_CHUNK` at a time in order, each chunk with its
 * digests and groupings: each set's outcome shows as its chunk answers, with how
 * many are done. Stop ends the run after the chunk in flight; a failed request
 * ends it with what answered kept and nothing sent twice; closing the dialog or
 * leaving the page mid-run, after the dialog asks, sends nothing more. The sets
 * not sent are shown as left out and nothing is queued behind the person's back.
 * While a run is in flight the preview is held, and the models are invalidated
 * once, when it ends. A list longer than one request (:data:`SETS_PAGE`) loads
 * page after page, saying how many are loaded, and Confirm waits for every page
 * (a page that fails offers a retry); at most :data:`CONFIRM_SETS` sets are
 * listed, and the dialog renders :data:`SHOWN_SETS` of them at a time through the
 * shared pager. The server judges and merges (`duplicate_set_previews`,
 * `merge_duplicates`); the dialog only shows.
 */
export function DuplicatesDialog({
  sets: opened = NO_SETS, selection, allCertain, allGroups: groupsGiven, certain: certainOnly = false, notes: given = [],
  onClose,
}: DuplicatesDialogProps): React.ReactElement {
  const t = usePartiesT();
  // The groups as the dialog opened on them: the list hands a new member of equal value on a page change.
  const [allGroups] = React.useState(groupsGiven);
  const everyCertain = allCertain !== undefined;
  const certain = certainOnly || everyCertain;
  // The dialog lists the sets it opened with, those found once among a selection, or every certain pair or every
  // group a list matched, page by page; a caller's later reads never change what is confirmed.
  const [found, setFound] = React.useState<readonly DuplicateSetInput[] | null>(
    selection || everyCertain || allGroups ? null : opened,
  );
  const derived = useAuthoredQuery(SelectionDuplicateSets, { ids: [...(selection ?? [])] }, {
    enabled: selection !== undefined && found === null,
  });
  const among = derived.data?.selection_duplicate_sets;
  React.useEffect(() => {
    if (found === null && among) setFound(among.sets.map((members) => ({ members })));
  }, [among, found]);
  // Every certain pair, or every group the list's read matched: one page after another, at most CONFIRM_SETS sets.
  const paged = everyCertain || allGroups !== undefined;
  const [pages, setPages] = React.useState<readonly ListedPage[]>([]);
  const lastPage = pages.at(-1);
  const pagedSets = React.useMemo(() => pages.flatMap((page) => page.sets).slice(0, CONFIRM_SETS), [pages]);
  const itemsRead = pages.reduce((count, page) => count + page.items, 0);
  // A listing ends on a short page, or at the bound; never on a count, which the list may have moved past.
  const pagesDone = lastPage !== undefined
    && (lastPage.ended || lastPage.items < SETS_PAGE || pagedSets.length >= CONFIRM_SETS);
  const pageVariables = allGroups
    ? groupSetsVariables(allGroups, lastPage?.after ?? null)
    : { limit: SETS_PAGE, after: lastPage?.after ?? null };
  const request = JSON.stringify(pageVariables);
  const listingDocument: AuthoredDocument = allGroups?.document ?? CertainSameAsPairs;
  // The one cast at the schema boundary: `listingDocument`, one of three documents, is typed `AuthoredDocument`, whose
  // variables are `never`.
  // The end-of-run invalidation reaches the listing's pages too, so a later dialog reads them again.
  const pageRead = useAuthoredQuery(listingDocument, pageVariables as never, {
    enabled: paged && !pagesDone, errorNotification: false, models: PARTY_MERGE_INVALIDATES,
  });
  // A page is listed only as this dialog read it: fetched since it mounted and not being read again. A page an
  // earlier dialog left in the cache (fresh for the app's stale time, so not refetched on its own) is read again
  // first; a disabled read, which may still answer from the cache, lists nothing and shows no error.
  const pageFresh = paged && pageRead.isFetchedAfterMount && !pageRead.isFetching;
  const pageData = pageFresh ? pageRead.data as ListedData | undefined : undefined;
  // The listing's error counts only while its read is enabled, as the previews' does: once every page is listed the
  // read is disabled, and its key (the page past the last) may still hold an error another reader met.
  const pageReadError = pageRead.error;
  const pageError = paged && !pagesDone ? pageReadError : null;
  // Typed apart: any authored document types the read's data `undefined`, so checking it destructured beside
  // `refetch` would narrow `refetch` to `never`.
  const cachedPage = pageRead.data as ListedData | undefined;
  const { isFetchedAfterMount: pageReadHere, isFetching: pageReading, refetch: readPage } = pageRead;
  React.useEffect(() => {
    if (paged && !pagesDone && cachedPage !== undefined && !pageReadHere && !pageReading) void readPage();
  }, [cachedPage, pageReadHere, pageReading, paged, pagesDone, readPage]);
  React.useEffect(() => {
    if (!pageData || pagesDone) return;
    setPages((listed) => (listed.some((page) => page.request === request)
      ? listed
      : [...listed, listedPage(pageData, request, allGroups, t)]));
  }, [allGroups, pageData, pagesDone, request, t]);
  const sets = React.useMemo(
    () => (paged ? pagedSets : (found ?? NO_SETS).slice(0, CONFIRM_SETS)),
    [found, paged, pagedSets],
  );
  const listed = paged ? pagesDone : found !== null;
  const unpaired = pages.reduce((count, page) => count + page.unpaired, 0);
  const blank = pages.reduce((count, page) => count + page.blank, 0);
  const notes = [
    ...given,
    ...(among?.explicit ? [t("duplicates.explicitSet")] : []),
    ...(among && !among.explicit && among.sets.length === 0
      ? [t("duplicates.noneFound", { count: String(selection?.length ?? 0) })]
      : []),
    ...(among && among.left_out > 0 && among.sets.length > 0
      ? [t("duplicates.leftOutUnpaired", { count: String(among.left_out) })]
      : []),
    ...(unpaired > 0 ? [t("duplicates.unpairedAll", { count: formatNumber(unpaired) })] : []),
    ...(blank > 0 ? [t("duplicates.blank", { count: formatNumber(blank) })] : []),
    // At the bound with more left to read, the next run lists the rest.
    ...(allCertain && pagesDone && pagedSets.length >= CONFIRM_SETS && lastPage?.items === SETS_PAGE
      ? [t("duplicates.firstOfAll", { count: formatNumber(CONFIRM_SETS), total: allCertain.total })]
      : []),
    ...(allGroups && pagesDone && pagedSets.length >= CONFIRM_SETS && lastPage?.items === SETS_PAGE && !lastPage.ended
      ? [t("duplicates.firstOfGroups", { count: formatNumber(itemsRead), total: formatNumber(allGroups.total) })]
      : []),
    // Listed to its end, the list held another number of groups than it counted when "Select all" was chosen.
    ...(allGroups && pagesDone && lastPage !== undefined && (lastPage.ended || lastPage.items < SETS_PAGE)
      && itemsRead !== allGroups.total
      ? [t("duplicates.listChanged", { count: formatNumber(itemsRead) })]
      : []),
  ];
  // The fields each set was grouped by, names only, when the list grouped any: the preview returns their rows.
  const groupings = React.useMemo(
    () => (sets.some((set) => set.grouping?.length) ? sets.map((set) => (set.grouping ?? []).map((level) => level.field)) : null),
    [sets],
  );
  // The previews load one page of SETS_PAGE sets after another; once all are in, the last page stays live (a set
  // that changed since the person saw it is refused by its digest when sent, whichever page it is on).
  const pageCount = Math.ceil(sets.length / SETS_PAGE);
  const [previewPages, setPreviewPages] = React.useState<readonly (readonly DuplicateSetPreview[])[]>([]);
  const previewing = Math.min(previewPages.length, Math.max(pageCount - 1, 0));
  const variables = React.useMemo(() => {
    const start = previewing * SETS_PAGE;
    const page = sets.slice(start, start + SETS_PAGE);
    return {
      sets: page.map((set) => [...set.members]),
      certain,
      groupings: groupings && groupings.slice(start, start + SETS_PAGE),
    };
  }, [certain, groupings, previewing, sets]);
  // A page is asked once its sets are all listed: the whole list, or a full page of it.
  const pageListed = listed || sets.length >= (previewing + 1) * SETS_PAGE;
  // Once confirmed the dialog keeps showing the sets as they were confirmed, each with its outcome; the preview is
  // held so the merges in flight do not refetch it.
  const [run, setRun] = React.useState<MergeRun | null>(null);
  const previewEnabled = variables.sets.length > 0 && pageListed && run === null;
  const preview = useAuthoredQuery(DuplicateSetPreviews, variables, {
    enabled: previewEnabled,
    models: PARTY_MERGE_INVALIDATES,
    errorNotification: false,
  });
  // A preview is used only as this dialog read it, as a listed page is: fetched since it opened and not being read
  // again. One an earlier dialog left in the cache (fresh for the app's stale time) is read again first, so a reopened
  // dialog's ticks and totals come from previews made after it opened.
  // A disabled read is none this dialog made, though it may still answer from the cache, as the listing's.
  const previewFresh = previewEnabled && preview.isFetchedAfterMount && !preview.isFetching;
  const previewPage = previewFresh ? preview.data?.duplicate_set_previews : undefined;
  // Its error counts only while the read is enabled, as its answers do and as the listing's error does: a disabled
  // read may still hold the error another reader of the same query met. Read on every render, so the query reports
  // its changes; a run disables the read, so a preview error met before the run is not shown during it.
  const previewReadError = preview.error;
  const previewError = previewEnabled ? previewReadError : null;
  // What the banner shows, and what holds Confirm while it shows: the dialog does not ask the person to confirm what
  // it has just said it could not read. A successful re-read clears it; a run disables both reads, so a run's own
  // controls never see it.
  const readError = previewError || pageError;
  const cachedPreview = preview.data;
  const previewReadHere = preview.isFetchedAfterMount;
  const previewReading = preview.isFetching;
  const readPreview = preview.refetch;
  React.useEffect(() => {
    if (previewEnabled && cachedPreview !== undefined && !previewReadHere && !previewReading) void readPreview();
  }, [cachedPreview, previewEnabled, previewReadHere, previewReading, readPreview]);
  React.useEffect(() => {
    if (!previewPage) return;
    setPreviewPages((pages) => (pages[previewing] === previewPage
      ? pages
      : [...pages.slice(0, previewing), previewPage, ...pages.slice(previewing + 1)]));
  }, [previewPage, previewing]);
  // Confirm waits for every page: the whole list is listed and each of its pages previewed.
  const loaded = listed && previewPages.length === pageCount;
  const [shownPage, setShownPage] = React.useState(1);
  const [merge] = useAuthoredMutation(MergeDuplicates);
  const invalidate = useInvalidateAuthoredModels();
  const invalidateResource = useInvalidate();
  const resourceInvalidations = useResourceInvalidates(PARTY_MERGE_RESOURCES);
  const stopping = React.useRef(false);
  const mounted = React.useRef(true);
  // Leaving the page mid-run sends nothing more: the chunk in flight answers and the run ends there.
  React.useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      stopping.current = true;
    };
  }, []);
  const [closing, setClosing] = React.useState(false);
  const running = run?.status === "running";
  const live = React.useMemo(() => previewPages.flat(), [previewPages]);
  const previews = run?.shown ?? live;
  // Each set's state when the person first saw it, and the person's ticks with the state each was made in.
  const [firstSeen, setFirstSeen] = React.useState<ReadonlyMap<number, string>>(new Map());
  React.useEffect(() => {
    setFirstSeen((seen) => {
      const added = live.flatMap((row, index) => (seen.has(index) ? [] : [[index, stateOf(row)] as const]));
      return added.length > 0 ? new Map([...seen, ...added]) : seen;
    });
  }, [live]);
  const [ticks, setTicks] = React.useState<ReadonlyMap<number, { checked: boolean; state: string }>>(new Map());
  // Whether a set has changed since it was first shown, or since the person's tick: it then waits for a new tick.
  const changed = (index: number): boolean => {
    const row = live[index];
    if (!row) return false;
    const tick = ticks.get(index);
    return tick ? tick.state !== stateOf(row) : firstSeen.has(index) && firstSeen.get(index) !== stateOf(row);
  };
  // A set merges only in a state the person saw: ticked in it, or untouched, unwarned and as first shown.
  const included = (index: number): boolean => {
    const row = live[index];
    if (!row || row.outcome != null || changed(index)) return false;
    const tick = ticks.get(index);
    return tick ? tick.checked : firstSeen.get(index) === stateOf(row) && !warned(row, sets[index]?.grouping);
  };
  const sending = live.filter((_row, index) => included(index));
  const merged = run ? [...run.outcomes.values()].some((row) => row.outcome === "MERGED") : false;

  const submit = React.useCallback(async () => {
    const queue = sending;
    stopping.current = false;
    let outcomes: ReadonlyMap<DuplicateSetPreview, DuplicateSetOutcome> = new Map();
    let status: MergeRun["status"] = "done";
    setRun({ shown: live, queue, outcomes, status: "running" });
    for (let start = 0; start < queue.length; start += MERGE_CHUNK) {
      if (stopping.current) {
        status = "stopped";
        break;
      }
      const chunk = queue.slice(start, start + MERGE_CHUNK);
      let answered: readonly DuplicateSetOutcome[] | undefined;
      try {
        // Each set's digest of what the person saw, judged with the same grouping fields: a set that changed
        // since is refused, to be looked at again.
        const response = await merge({
          sets: chunk.map((row) => row.members.map((member) => member.id)),
          digests: chunk.map((row) => row.digest),
          certain,
          groupings: groupings && chunk.map((row) => groupings[live.indexOf(row)] ?? []),
        }, { errorNotification: false });
        answered = response?.merge_duplicates;
      } catch {
        answered = undefined;
      }
      if (!answered) {
        status = "failed";
        break;
      }
      // One outcome answers each sent set, in the order sent.
      const next = new Map(outcomes);
      chunk.forEach((row, index) => {
        const outcome = answered[index];
        if (outcome) next.set(row, outcome);
      });
      outcomes = next;
      if (mounted.current) setRun({ shown: live, queue, outcomes, status: "running" });
    }
    // Once, when the run ends or stops: the pages behind read the merges' result together, their authored reads and
    // their resource lists (rows and totals) alike.
    invalidate(PARTY_MERGE_INVALIDATES);
    for (const params of resourceInvalidations) void invalidateResource(params);
    if (mounted.current) setRun({ shown: live, queue, outcomes, status });
  }, [certain, groupings, invalidate, invalidateResource, live, merge, resourceInvalidations, sending]);
  const close = (): void => {
    if (running) setClosing(true);
    else onClose(merged);
  };

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) close(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg" placement="center">
          <Dialog.Header>
            <Dialog.Title>{t("duplicates.title")}</Dialog.Title>
            <Dialog.Description>{t("duplicates.description")}</Dialog.Description>
          </Dialog.Header>
          <Dialog.Body className="space-y-4">
            <Alert tone="info">{t("duplicates.valueRule")} {t("duplicates.undo")}</Alert>
            {notes.map((note) => <Alert key={note} tone="info">{note}</Alert>)}
            {derived.error ? <ErrorBanner description={derived.error.message || t("duplicates.error")} /> : null}
            {readError ? (
              <ErrorBanner
                // Said by what failed: a re-read of previews already shown, a page not yet shown, or the first read.
                description={t(previewError && loaded
                  ? "duplicates.rereadError"
                  : live.length > 0 ? "duplicates.pageError" : "duplicates.error")}
                actions={
                  <Button type="button" size="sm" variant="secondary"
                    onClick={() => void (pageError ? pageRead.refetch() : preview.refetch())}>
                    {t("duplicates.retry")}
                  </Button>
                }
              />
            ) : null}
            {run ? <RunLine run={run} /> : null}
            {/* A paged dialog says how far it has read from its first render until every page is listed and previewed. */}
            {!run && paged && !loaded ? (
              <>
                <Alert tone="info">
                  {allGroups && !pagesDone
                    ? t("duplicates.loadedGroupsOf", { loaded: formatNumber(itemsRead), total: formatNumber(allGroups.total) })
                    : t("duplicates.loadedOf", {
                      loaded: formatNumber(live.length),
                      total: allCertain && !pagesDone ? allCertain.total : formatNumber(sets.length),
                    })}
                </Alert>
                {readError ? null : <LoadingPanel density="inline" loadingLabel={t("duplicates.loading")} />}
              </>
            ) : !run && !loaded && live.length > 0 ? (
              <Alert tone="info">
                {t("duplicates.loadedOf", { loaded: formatNumber(live.length), total: formatNumber(sets.length) })}
              </Alert>
            ) : null}
            {!run && loaded && live.length > 0 && (paged || live.length > SETS_PAGE) ? (
              <Alert tone="info">
                {t("duplicates.totals", {
                  count: formatNumber(live.length),
                  merge: formatNumber(sending.length),
                  look: formatNumber(live.length - sending.length),
                })}
              </Alert>
            ) : null}
            {!paged && (preview.isFetching || derived.isFetching) && previews.length === 0
              ? <LoadingPanel loadingLabel={t("duplicates.loading")} />
              : null}
            {previews.length > SHOWN_SETS ? (
              <Pager page={shownPage} pageSize={SHOWN_SETS} total={previews.length} unit={t("duplicates.pagerUnit")}
                labelElement="span" onPageChange={setShownPage} />
            ) : null}
            {previews.slice((shownPage - 1) * SHOWN_SETS, shownPage * SHOWN_SETS).map((row, shown) => {
              const index = (shownPage - 1) * SHOWN_SETS + shown;
              return (
                <DuplicateSetSection
                  key={sets[index]?.members.join(":") ?? index}
                  preview={row}
                  heading={sets[index]?.heading}
                  grouping={sets[index]?.grouping}
                  outcome={run?.outcomes.get(row)}
                  include={run || row.outcome != null ? undefined : {
                    checked: included(index),
                    changed: changed(index),
                    onChange: (checked) => setTicks((current) => new Map(current).set(index, { checked, state: stateOf(row) })),
                  }}
                  left={run !== null && !running && row.outcome == null && !run.outcomes.has(row)}
                />
              );
            })}
          </Dialog.Body>
          <Dialog.Footer>
            {running && closing ? (
              <>
                <span className="mr-auto text-13 text-fg-muted">{t("duplicates.closeRunning")}</span>
                <Button type="button" variant="secondary" onClick={() => setClosing(false)}>{t("duplicates.keepMerging")}</Button>
                <Button
                  type="button"
                  variant="danger"
                  onClick={() => {
                    stopping.current = true;
                    onClose(merged);
                  }}
                >
                  {t("duplicates.stopAndClose")}
                </Button>
              </>
            ) : running ? (
              <Button
                type="button"
                variant="secondary"
                disabled={stopping.current}
                onClick={() => {
                  stopping.current = true;
                  setRun((current) => current && { ...current });
                }}
              >
                {t(stopping.current ? "duplicates.stopping" : "duplicates.stop")}
              </Button>
            ) : run ? (
              <Button type="button" variant="secondary" onClick={() => onClose(merged)}>{t("duplicates.done")}</Button>
            ) : (
              <>
                <Button type="button" variant="secondary" onClick={() => onClose(false)}>
                  {t("duplicates.cancel")}
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  disabled={!loaded || sending.length === 0 || preview.isFetching || Boolean(readError)}
                  onClick={() => void submit()}
                >
                  {t("duplicates.confirm", { count: sending.length })}
                </Button>
              </>
            )}
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function DuplicateSetSection({
  preview,
  heading,
  grouping,
  outcome,
  include,
  left = false,
}: {
  preview: DuplicateSetPreview;
  heading?: string;
  grouping?: DuplicateSetInput["grouping"];
  outcome?: DuplicateSetOutcome;
  /** The set's Include toggle, while it may merge and nothing has been sent; ``changed`` since the person saw it. */
  include?: { checked: boolean; changed: boolean; onChange: (checked: boolean) => void };
  /** The person left this set out of the merge that was sent. */
  left?: boolean;
}): React.ReactElement {
  const t = usePartiesT();
  const includeId = React.useId();
  const routeHref = useRouteHref();
  const sources = useEnumOptions("parties.PartyStatement", "source_kind", { casing: "upper" });
  const survivorId = preview.survivor?.id ?? null;
  const members = preview.members;
  const moved = React.useMemo(() => movedMembers(preview, grouping), [grouping, preview]);
  const memberColumns = React.useMemo<readonly ListColumn<DuplicateSetMember>[]>(() => [
    {
      field: "display_name",
      header: t("duplicates.member"),
      sortable: false,
      render: (row) => (
        <span className="flex flex-wrap items-center gap-2">
          {row.display_name}
          {row.id === survivorId ? <Chip tone="success">{t("duplicates.survives")}</Chip> : null}
          {moved.has(row.id) ? <Chip tone="warning">{t("duplicates.moved")}</Chip> : null}
        </span>
      ),
    },
    { field: "created_at", header: t("duplicates.created"), sortable: false, render: (row) => <DateTimeRead value={row.created_at} /> },
    { field: "first_met_note", header: t("duplicates.firstMet"), sortable: false },
    { field: "addresses", header: t("duplicates.addresses"), sortable: false, render: (row) => memberAddresses(row).join(", ") },
    {
      field: "statements",
      header: t("duplicates.sources"),
      sortable: false,
      render: (row) => (
        <span className="flex flex-wrap gap-1">
          {memberSources(row).map((kind) => <Chip key={kind}>{optionLabel(sources, kind)}</Chip>)}
        </span>
      ),
    },
  ], [moved, sources, survivorId, t]);
  const disagreementRows = React.useMemo(
    () => preview.disagreements.map((row) => ({ id: row.field, ...row })),
    [preview.disagreements],
  );
  type DisagreementRow = (typeof disagreementRows)[number];
  const disagreementColumns = React.useMemo<readonly ListColumn<DisagreementRow>[]>(() => [
    { field: "field", header: t("duplicates.field"), sortable: false, render: (row) => t(`merge.field.${row.field}`) },
    ...members.map((member, index) => ({
      field: `values.${index}`,
      header: member.display_name,
      sortable: false,
      render: (row: DisagreementRow) => row.values[index] ?? t("merge.fields.empty"),
    })),
    { field: "shown", header: t("duplicates.shown"), sortable: false, render: (row) => row.shown ?? t("merge.fields.empty") },
  ], [members, t]);

  return (
    <section className="space-y-2 rounded-6 border border-border-subtle p-3">
      {heading ? <h3 className="text-13 font-semibold text-fg">{heading}</h3> : null}
      {include ? (
        <div className="flex flex-wrap items-center gap-2 text-13">
          <Checkbox id={includeId} aria-label={t("duplicates.include")} checked={include.checked}
            onCheckedChange={(checked) => include.onChange(checked === true)} />
          <label htmlFor={includeId} className="font-medium text-fg">{t("duplicates.include")}</label>
          {include.changed ? <span className="text-fg-muted">{t("duplicates.includeChanged")}</span>
            : warned(preview, grouping) ? <span className="text-fg-muted">{t("duplicates.includeWarned")}</span> : null}
        </div>
      ) : null}
      {left ? <Alert tone="info">{t("duplicates.outcome.left")}</Alert> : null}
      {outcome ? <OutcomeLine outcome={outcome} /> : null}
      {!outcome && preview.outcome ? <Alert tone="warning">{t("duplicates.refused", { reason: preview.reason })}</Alert> : null}
      {!outcome && survivorId ? <p className="text-13 text-fg-muted">{t("duplicates.survivorRule")}</p> : null}
      {/* The fills are no warning: the survivor takes each field only one record states, and nothing is lost. */}
      {!outcome && preview.fills.length > 0 ? (
        <p className="text-13 text-fg-muted">
          {t("duplicates.gains", { fields: preview.fills.map((row) => t(`merge.field.${row.field}`)).join(", ") })}
        </p>
      ) : null}
      <RowsListView<DuplicateSetMember> rows={members} columns={memberColumns} scope="local" presentation="embedded" />
      {disagreementRows.length > 0 && !outcome ? (
        <div className="space-y-2">
          <p className="text-13 font-semibold text-fg">{t("duplicates.disagree")}</p>
          <RowsListView<DisagreementRow> rows={disagreementRows} columns={disagreementColumns} scope="local" presentation="embedded" />
          {survivorId ? (
            <p className="flex flex-wrap gap-3 text-13">
              {members.filter((member) => member.id !== survivorId).map((member) => (
                <NavLink key={member.id} href={routeHref("parties.merge", { left: survivorId, right: member.id })}>
                  {t("duplicates.compare", { name: member.display_name })}
                </NavLink>
              ))}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

/** How far a run has come, and, once it ended early, why the rest was not sent. */
function RunLine({ run }: { run: MergeRun }): React.ReactElement {
  const t = usePartiesT();
  const counts = { done: String(run.outcomes.size), total: String(run.queue.length) };
  if (run.status === "failed") return <Alert tone="danger">{t("duplicates.runFailed", counts)}</Alert>;
  if (run.status === "stopped") return <Alert tone="warning">{t("duplicates.runStopped", counts)}</Alert>;
  return <Alert tone={run.status === "done" ? "success" : "info"}>{t("duplicates.progress", counts)}</Alert>;
}

function OutcomeLine({ outcome }: { outcome: DuplicateSetOutcome }): React.ReactElement {
  const t = usePartiesT();
  const routeHref = useRouteHref();
  const survivor = outcome.survivor;
  if (outcome.outcome === "MERGED" && survivor) {
    const route = survivor.concrete_kind === "ORGANIZATION" ? "parties.organizations.record" : "parties.people.record";
    return (
      <Alert tone="success">
        <NavLink href={routeHref(route, { id: survivor.id })}>{t("duplicates.outcome.merged", { name: survivor.display_name })}</NavLink>
      </Alert>
    );
  }
  if (outcome.outcome === "NOT_CERTAIN") return <Alert tone="warning">{t("duplicates.outcome.notCertain")}</Alert>;
  if (outcome.outcome === "STOPPED") return <Alert tone="warning">{t("duplicates.outcome.stopped")}</Alert>;
  return <Alert tone="danger">{t("duplicates.outcome.refused", { reason: outcome.reason })}</Alert>;
}

/** Every address a source states for a member, the one it holds included, once each. */
function memberAddresses(member: DuplicateSetMember): string[] {
  return [...new Set(member.statements.flatMap((statement) => (statement.handle ? [statement.handle.value] : [])))];
}

/** A set's state as the person sees it: its outcome and the digest of everything shown of it. */
function stateOf(preview: DuplicateSetPreview): string {
  return `${preview.outcome ?? ""}:${preview.digest}`;
}

/**
 * Whether a set carries a warning the person should look at before merging it:
 * its records state two different values of a field, or one moved from its group.
 * A fill (one value stated, the rest empty) is no warning: the merge loses nothing.
 */
function warned(preview: DuplicateSetPreview | undefined, grouping: DuplicateSetInput["grouping"]): boolean {
  return preview !== undefined && (preview.disagreements.length > 0 || movedMembers(preview, grouping).size > 0);
}

/**
 * The members whose current value of a field the list grouped the set by is no
 * longer the group's. The fresh read returns a grouping row for each such field
 * that is a text merge field, whatever the members hold, besides the rows of every
 * merge field whose values differ, so a member that moved shows, one whose value
 * was cleared and a set whose members all moved alike included. A relation or a
 * date-bucket group has no row and is not marked; a field the reader may not read
 * on any member has none either.
 */
function movedMembers(preview: DuplicateSetPreview, grouping: DuplicateSetInput["grouping"]): ReadonlySet<string> {
  const moved = new Set<string>();
  const differing = [...preview.grouping, ...preview.disagreements, ...preview.fills];
  for (const level of grouping ?? []) {
    const row = level.key === null ? undefined : differing.find((item) => item.field === level.field);
    preview.members.forEach((member, index) => {
      if (row && row.values[index] !== level.key) moved.add(member.id);
    });
  }
  return moved;
}

/** The kinds of source that state a member's addresses, once each. */
function memberSources(member: DuplicateSetMember): string[] {
  return [...new Set(member.statements.map((statement) => String(statement.source_kind)))];
}
