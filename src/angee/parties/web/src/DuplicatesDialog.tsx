import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Alert, Button, Chip, Dialog, ErrorBanner, LoadingPanel, NavLink, RowsListView, defaultWidgets, optionLabel,
  useEnumOptions, useRouteHref, type ListColumn,
} from "@angee/ui";

import {
  DuplicateSetPreviews,
  MergeDuplicates,
  PARTY_MERGE_INVALIDATES,
  type DuplicateSetMember,
  type DuplicateSetOutcome,
  type DuplicateSetPreview,
} from "./documents";
import { usePartiesT } from "./i18n";

const DateTimeRead = defaultWidgets.datetime.read;

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

interface MergeResult {
  shown: readonly DuplicateSetPreview[];
  outcomes: ReadonlyMap<DuplicateSetPreview, DuplicateSetOutcome>;
}

export interface DuplicatesDialogProps {
  sets: readonly DuplicateSetInput[];
  /** The Review page's certain pairs: each set is re-checked as still certain when merged. */
  certain?: boolean;
  onClose: (merged: boolean) => void;
}

/**
 * The batch merge's one dialog. It lists each set with what tells its records
 * apart (addresses, sources, dates), which record survives and why, and the fields
 * they disagree on with the value that will show; one confirm merges exactly the
 * listed sets and the dialog then shows each set's outcome. The server judges and
 * merges (`duplicate_set_previews`, `merge_duplicates`); the dialog only shows.
 */
export function DuplicatesDialog({ sets: opened, certain = false, onClose }: DuplicatesDialogProps): React.ReactElement {
  const t = usePartiesT();
  // The dialog lists the sets it opened with; a caller's later reads never change what is confirmed.
  const [sets] = React.useState(opened);
  const variables = React.useMemo(
    () => ({ sets: sets.map((set) => [...set.members]), certain }),
    [certain, sets],
  );
  // After the merge the dialog keeps showing the sets as they were confirmed, each with its outcome.
  const [result, setResult] = React.useState<MergeResult | null>(null);
  const preview = useAuthoredQuery(DuplicateSetPreviews, variables, {
    enabled: sets.length > 0 && result === null,
    models: PARTY_MERGE_INVALIDATES,
  });
  const [merge, mergeState] = useAuthoredMutation(MergeDuplicates, { invalidateModels: PARTY_MERGE_INVALIDATES });
  const live = preview.data?.duplicate_set_previews ?? [];
  const previews = result?.shown ?? live;
  const mergeable = live.filter((row) => row.outcome == null);
  const merged = result ? [...result.outcomes.values()].some((row) => row.outcome === "MERGED") : false;

  const submit = React.useCallback(async () => {
    const sent = mergeable;
    const response = await merge({ sets: sent.map((row) => row.members.map((member) => member.id)), certain });
    const answered = response?.merge_duplicates;
    if (!answered) return;
    // One outcome answers each sent set, in the order sent.
    const outcomes = new Map<DuplicateSetPreview, DuplicateSetOutcome>();
    sent.forEach((row, index) => {
      const outcome = answered[index];
      if (outcome) outcomes.set(row, outcome);
    });
    setResult({ shown: live, outcomes });
  }, [certain, live, merge, mergeable]);

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !mergeState.fetching) onClose(merged); }}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg" placement="center">
          <Dialog.Header>
            <Dialog.Title>{t("duplicates.title")}</Dialog.Title>
            <Dialog.Description>{t("duplicates.description")}</Dialog.Description>
          </Dialog.Header>
          <Dialog.Body className="space-y-4">
            <Alert tone="info">{t("duplicates.valueRule")} {t("duplicates.undo")}</Alert>
            {preview.error ? <ErrorBanner description={t("duplicates.error")} /> : null}
            {mergeState.error ? <ErrorBanner description={t("duplicates.mergeError")} /> : null}
            {preview.isFetching && previews.length === 0 ? <LoadingPanel loadingLabel={t("duplicates.loading")} /> : null}
            {previews.map((row, index) => (
              <DuplicateSetSection
                key={sets[index]?.members.join(":") ?? index}
                preview={row}
                heading={sets[index]?.heading}
                grouping={sets[index]?.grouping}
                outcome={result?.outcomes.get(row)}
              />
            ))}
          </Dialog.Body>
          <Dialog.Footer>
            {result ? (
              <Button type="button" variant="secondary" onClick={() => onClose(merged)}>{t("duplicates.done")}</Button>
            ) : (
              <>
                <Button type="button" variant="secondary" disabled={mergeState.fetching} onClick={() => onClose(false)}>
                  {t("duplicates.cancel")}
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  pending={mergeState.fetching}
                  disabled={mergeable.length === 0 || mergeState.fetching || preview.isFetching}
                  onClick={() => void submit()}
                >
                  {t("duplicates.confirm", { count: mergeable.length })}
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
}: {
  preview: DuplicateSetPreview;
  heading?: string;
  grouping?: DuplicateSetInput["grouping"];
  outcome?: DuplicateSetOutcome;
}): React.ReactElement {
  const t = usePartiesT();
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
      {outcome ? <OutcomeLine outcome={outcome} /> : null}
      {!outcome && preview.outcome ? <Alert tone="warning">{t("duplicates.refused", { reason: preview.reason })}</Alert> : null}
      {!outcome && survivorId ? <p className="text-13 text-fg-muted">{t("duplicates.survivorRule")}</p> : null}
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

/**
 * The members whose current value of a field the list grouped the set by is no
 * longer the group's: the fresh read's disagreements hold each member's value of
 * every merge field they disagree on, so a stale grouping shows at no extra read.
 */
function movedMembers(preview: DuplicateSetPreview, grouping: DuplicateSetInput["grouping"]): ReadonlySet<string> {
  const moved = new Set<string>();
  for (const level of grouping ?? []) {
    const disagreement = level.key === null ? undefined : preview.disagreements.find((row) => row.field === level.field);
    preview.members.forEach((member, index) => {
      if (disagreement && disagreement.values[index] !== level.key) moved.add(member.id);
    });
  }
  return moved;
}

/** The kinds of source that state a member's addresses, once each. */
function memberSources(member: DuplicateSetMember): string[] {
  return [...new Set(member.statements.map((statement) => String(statement.source_kind)))];
}
