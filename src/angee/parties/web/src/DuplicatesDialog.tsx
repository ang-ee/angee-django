import * as React from "react";
import { useResourceInvalidates } from "@angee/metadata";
import { useAuthoredMutation, useAuthoredQuery, useInvalidateAuthoredModels } from "@angee/refine";
import { useInvalidate } from "@refinedev/core";
import {
  Alert, Button, Checkbox, Chip, Dialog, ErrorBanner, LoadingPanel, NavLink, RowsListView, defaultWidgets, optionLabel,
  useEnumOptions, useRouteHref, type ListColumn,
} from "@angee/ui";

import {
  DuplicateSetPreviews,
  MergeDuplicates,
  PARTY_MERGE_INVALIDATES,
  PARTY_MERGE_RESOURCES,
  SelectionDuplicateSets,
  type DuplicateSetMember,
  type DuplicateSetOutcome,
  type DuplicateSetPreview,
} from "./documents";
import { usePartiesT } from "./i18n";

const DateTimeRead = defaultWidgets.datetime.read;

const NO_SETS: readonly DuplicateSetInput[] = [];

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

/** One confirmed run: the sets as confirmed, their outcomes as the chunks answer, and how it ended. */
interface MergeRun {
  shown: readonly DuplicateSetPreview[];
  queue: readonly DuplicateSetPreview[];
  outcomes: ReadonlyMap<DuplicateSetPreview, DuplicateSetOutcome>;
  status: "running" | "done" | "stopped" | "failed";
}

export interface DuplicatesDialogProps {
  sets: readonly DuplicateSetInput[];
  /**
   * Records selected on an ungrouped list: the server finds the duplicate sets
   * among them (``selection_duplicate_sets``) and the dialog lists those, said
   * what was left out; ``sets`` is then unused.
   */
  selection?: readonly string[];
  /** The Review page's certain pairs: each set is re-checked as still certain when merged. */
  certain?: boolean;
  /**
   * Lines about the sets around these ones: how the next batch is reached (the
   * Review page's certain pairs, a selection over the batch cap) and what a list
   * selection left out (records with no one to merge with, groups without a value).
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
 * once, when it ends. The server judges and merges (`duplicate_set_previews`,
 * `merge_duplicates`); the dialog only shows.
 */
export function DuplicatesDialog({
  sets: opened, selection, certain = false, notes: given = [], onClose,
}: DuplicatesDialogProps): React.ReactElement {
  const t = usePartiesT();
  // The dialog lists the sets it opened with, or those found once among a selection; a caller's later reads never
  // change what is confirmed.
  const [found, setFound] = React.useState<readonly DuplicateSetInput[] | null>(selection ? null : opened);
  const derived = useAuthoredQuery(SelectionDuplicateSets, { ids: [...(selection ?? [])] }, {
    enabled: selection !== undefined && found === null,
  });
  const among = derived.data?.selection_duplicate_sets;
  React.useEffect(() => {
    if (found === null && among) setFound(among.sets.map((members) => ({ members })));
  }, [among, found]);
  const sets = found ?? NO_SETS;
  const notes = [
    ...given,
    ...(among?.explicit ? [t("duplicates.explicitSet")] : []),
    ...(among && !among.explicit && among.sets.length === 0
      ? [t("duplicates.noneFound", { count: String(selection?.length ?? 0) })]
      : []),
    ...(among && among.left_out > 0 && among.sets.length > 0
      ? [t("duplicates.leftOutUnpaired", { count: String(among.left_out) })]
      : []),
  ];
  // The fields each set was grouped by, names only, when the list grouped any: the preview returns their rows.
  const groupings = React.useMemo(
    () => (sets.some((set) => set.grouping?.length) ? sets.map((set) => (set.grouping ?? []).map((level) => level.field)) : null),
    [sets],
  );
  const variables = React.useMemo(
    () => ({ sets: sets.map((set) => [...set.members]), certain, groupings }),
    [certain, groupings, sets],
  );
  // Once confirmed the dialog keeps showing the sets as they were confirmed, each with its outcome; the preview is
  // held so the merges in flight do not refetch it.
  const [run, setRun] = React.useState<MergeRun | null>(null);
  const preview = useAuthoredQuery(DuplicateSetPreviews, variables, {
    enabled: sets.length > 0 && run === null,
    models: PARTY_MERGE_INVALIDATES,
  });
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
  const live = preview.data?.duplicate_set_previews ?? [];
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
            {preview.error ? <ErrorBanner description={t("duplicates.error")} /> : null}
            {run ? <RunLine run={run} /> : null}
            {(preview.isFetching || derived.isFetching) && previews.length === 0
              ? <LoadingPanel loadingLabel={t("duplicates.loading")} />
              : null}
            {previews.map((row, index) => (
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
            ))}
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
                  disabled={sending.length === 0 || preview.isFetching}
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
