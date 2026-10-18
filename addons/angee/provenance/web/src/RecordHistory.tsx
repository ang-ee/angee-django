import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  Button,
  Chip,
  Code,
  CodeBlock,
  CollapsibleIcon,
  CollapsiblePanel,
  CollapsibleRoot,
  CollapsibleTrigger,
  ComparisonRows,
  EmptyState,
  ErrorBanner,
  LoadingPanel,
  RecordActionBar,
  RecordReference,
  StepList,
  ACTIVITY_TAB_ID,
  optionToken,
  titleCase,
  useActionResultMutation,
  type ActivityEntry,
  type ActivityRecord,
  type ActivityWindow,
  type ActionDescriptor,
  type ComparisonRow,
  type StepListItem,
} from "@angee/ui";
import { useCallback, useMemo, useState, type ReactElement, type ReactNode } from "react";

import {
  RecordHistoryDocument,
  type HistoryEntry,
  type HistoryStatement,
  type HistorySummaryEntry,
  type RecordHistory,
} from "./documents.console";
import { entryTone, historyValue, statementVerb } from "./history-entries";
import { useProvenanceT, type ProvenanceT } from "./i18n";

/** Changesets per page; the server caps a page at 100. */
export const HISTORY_PAGE_SIZE = 25;

export interface UseRecordHistoryOptions {
  enabled?: boolean;
  limit?: number;
  offset?: number;
  dataProviderName?: string;
}

/** One record's changesets through the shared `history` read; live changes to the record refetch it. */
export function useRecordHistory(model: string, id: string | null | undefined, options: UseRecordHistoryOptions = {}) {
  const active = (options.enabled ?? true) && Boolean(id);
  const records = useMemo(() => (id ? [{ model, id }] : []), [id, model]);
  return useAuthoredQuery(
    RecordHistoryDocument,
    { model, id: id ?? "", limit: options.limit ?? HISTORY_PAGE_SIZE, offset: options.offset ?? 0 },
    {
      ...(options.dataProviderName !== undefined ? { dataProviderName: options.dataProviderName } : {}),
      enabled: active,
      keepPreviousData: true,
      models: [model],
      records,
    },
  );
}

export interface RecordHistoryTimelineProps {
  model: string;
  id: string | null | undefined;
  dataProviderName?: string;
  /** One line per changeset linking to the record's Activity tab. */
  compact?: boolean;
}

/** The timeline with its read, paging and the Revert verb bound to one record. */
export function RecordHistoryTimeline({
  model,
  id,
  dataProviderName,
  compact = false,
}: RecordHistoryTimelineProps): ReactElement {
  const t = useProvenanceT();
  const [offset, setOffset] = useState(0);
  const query = useRecordHistory(model, id, {
    offset,
    ...(dataProviderName !== undefined ? { dataProviderName } : {}),
  });
  const [revert, revertState] = useActionResultMutation<ActionFieldName>("revert_changeset", {
    ...(dataProviderName !== undefined ? { dataProviderName } : {}),
    invalidateModels: [model],
  });
  if (!id) {
    return (
      <EmptyState
        icon="activity"
        title={t("history.noRecordTitle")}
        description={t("history.noRecordDescription")}
        className="min-h-48 p-4"
      />
    );
  }
  const history = query.data?.history ?? null;
  if (query.error && history === null) {
    return <ErrorBanner title={t("history.unavailable")} description={query.error.message} />;
  }
  if (history === null) {
    return query.isLoading
      ? <LoadingPanel message={t("history.loading")} density="inline" />
      : <ErrorBanner title={t("history.unavailable")} description={null} />;
  }
  if (history.total === 0) {
    return (
      <EmptyState
        icon="activity"
        title={t("history.emptyTitle")}
        description={t("history.emptyDescription")}
        className="min-h-48 p-4"
      />
    );
  }
  return (
    <HistoryTimeline
      history={history}
      compact={compact}
      fetching={query.isFetching}
      offset={offset}
      onPage={compact ? undefined : setOffset}
      onRevert={compact ? undefined : (entry) => revert(id, { model, changeset: entry.id })}
      reverting={revertState.fetching}
    />
  );
}

export interface HistoryTimelineProps {
  history: RecordHistory;
  compact?: boolean;
  fetching?: boolean;
  offset?: number;
  onPage?: (offset: number) => void;
  onRevert?: (entry: HistoryEntry) => Promise<void> | void;
  reverting?: boolean;
}

/** Presentation over one loaded page: the workflows step list renders changesets as it renders runs. */
export function HistoryTimeline({
  history,
  compact = false,
  fetching = false,
  offset = 0,
  onPage,
  onRevert,
  reverting = false,
}: HistoryTimelineProps): ReactElement {
  const t = useProvenanceT();
  const items = history.entries.map((entry): StepListItem => ({
    id: entry.id,
    state: "done",
    tone: entryTone(entry),
    title: <EntryTitle entry={entry} t={t} />,
    ...(optionToken(entry.outcome) === "failure" ? { outcome: t("history.outcome.failure") } : {}),
    timestamp: entry.recorded_at,
    detail: compact
      ? <RecordReference model={history.model} id={history.id} search={{ chatterTab: ACTIVITY_TAB_ID }} label={t("history.openTab")} />
      : <EntrySummary entry={entry} t={t} />,
    ...(compact ? {} : {
      children: <EntryDetails entry={entry} record={history} onRevert={onRevert} reverting={reverting} t={t} />,
    }),
  }));
  const pager = onPage && history.total > HISTORY_PAGE_SIZE ? (
    <div className="flex items-center gap-2">
      <Button size="sm" variant="ghost" disabled={offset === 0 || fetching}
        onClick={() => onPage(Math.max(0, offset - HISTORY_PAGE_SIZE))}>
        {t("history.newer")}
      </Button>
      <Button size="sm" variant="ghost" disabled={offset + HISTORY_PAGE_SIZE >= history.total || fetching}
        onClick={() => onPage(offset + HISTORY_PAGE_SIZE)}>
        {t("history.older")}
      </Button>
    </div>
  ) : null;
  return (
    <div className="grid min-w-0 gap-3">
      <StepList items={items} />
      {pager}
    </div>
  );
}

/** Compact facts for a changeset already loaded by Activity; no second read or pager. */
export function HistoryEntrySummary({ entry, model, id }: {
  entry: HistorySummaryEntry;
  model: string;
  id: string;
}): ReactElement {
  const t = useProvenanceT();
  return <div className="grid gap-1">{entry.change_reason ? <p className="text-fg">{entry.change_reason}</p> : null}
    <span className="text-xs text-fg-muted"><RecordReference model={model} id={id}
      search={{ chatterTab: ACTIVITY_TAB_ID }} label={t("history.openTab")} /></span></div>;
}

/** Provenance's `record#activity` hook: detailed changes, statements and Revert stay together. */
export function useRecordHistoryEntries(record: ActivityRecord, window: ActivityWindow): readonly ActivityEntry[] {
  const query = useRecordHistory(record.model, record.id, { enabled: record.history, limit: window.limit });
  const [revert, revertState] = useActionResultMutation<ActionFieldName>("revert_changeset", {
    invalidateModels: [record.model],
  });
  const history = query.data?.history ?? null;
  const onRevert = useCallback((entry: HistoryEntry) =>
    revert(record.id, { model: record.model, changeset: entry.id }), [record.id, record.model, revert]);
  return useMemo(() => history?.entries.map((entry): ActivityEntry => ({
    at: entry.recorded_at,
    kind: entry.source_kind && optionToken(entry.source_kind) !== "person" ? "source-event" : "change",
    actor: <EntryActor entry={entry} />,
    title: <ActivityEntryTitle entry={entry} />,
    body: <div className="grid gap-2"><HistoryEntrySummary entry={entry} model={history.model} id={history.id} />
      <ActivityHistoryDetails entry={entry} record={history} onRevert={onRevert} reverting={revertState.fetching} /></div>,
    reference: null,
  })) ?? [], [history, onRevert, revertState.fetching]);
}

function ActivityHistoryDetails(props: Omit<Parameters<typeof EntryDetails>[0], "t">): ReactElement | null {
  return <EntryDetails {...props} t={useProvenanceT()} />;
}

function EntryActor({ entry }: { entry: HistorySummaryEntry }): ReactElement {
  const t = useProvenanceT();
  return entry.actor_model && entry.actor_id
    ? <RecordReference model={entry.actor_model} id={entry.actor_id} />
    : <span>{entry.actor ?? t("history.system")}</span>;
}

function ActivityEntryTitle({ entry }: { entry: HistorySummaryEntry }): ReactElement {
  const t = useProvenanceT();
  return <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
    <span>{activityLabel(t, entry.activity)}</span>
    <Badge tone="neutral">{actionLabel(t, entry.action)}</Badge>
    {entry.source_kind ? <Chip size="sm" tone="info">{titleCase(optionToken(entry.source_kind))}</Chip> : null}
  </span>;
}

/** "<actor> <activity> · <action>": who wrote, how the write came about, what it did. */
function EntryTitle({ entry, t }: { entry: HistorySummaryEntry; t: ProvenanceT }): ReactElement {
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
      <EntryActor entry={entry} />
      <span className="font-normal text-fg-muted">{activityLabel(t, entry.activity)}</span>
      <Badge tone="neutral">{actionLabel(t, entry.action)}</Badge>
      {entry.source_kind ? <Chip size="sm" tone="info">{titleCase(optionToken(entry.source_kind))}</Chip> : null}
    </span>
  );
}

function EntrySummary({ entry, t }: { entry: HistoryEntry; t: ProvenanceT }): ReactElement {
  const counts = [
    entry.changes.length ? t("history.changes", { count: entry.changes.length }) : null,
    entry.statements.length ? t("history.statements", { count: entry.statements.length }) : null,
  ].filter((part): part is string => part !== null);
  return (
    <div className="grid gap-0.5">
      {entry.change_reason ? <p className="text-fg">{entry.change_reason}</p> : null}
      {counts.length ? <p className="text-fg-muted">{counts.join(" · ")}</p> : null}
    </div>
  );
}

function HistoryValueCell({ value, t }: { value: unknown; t: ProvenanceT }): ReactElement {
  const rendered = historyValue(value);
  if (rendered.kind === "empty") return <span className="text-fg-subtle">{t("history.empty")}</span>;
  if (rendered.kind === "json") return <CodeBlock>{rendered.text}</CodeBlock>;
  return <span className="[overflow-wrap:anywhere]">{rendered.text}</span>;
}

/** Field deltas, statement versions, merge facts and the Revert verb, folded under one trigger. */
function EntryDetails({ entry, record, onRevert, reverting, t }: {
  entry: HistoryEntry;
  record: RecordHistory;
  onRevert: HistoryTimelineProps["onRevert"];
  reverting: boolean;
  t: ProvenanceT;
}): ReactElement | null {
  const rows: ComparisonRow[] = entry.changes.map((change) => ({
    key: `${change.model}:${change.id}:${change.field}`,
    label: (
      <span className="grid gap-0.5">
        {change.model !== record.model || change.id !== record.id
          ? <RecordReference model={change.model} id={change.id} /> : null}
        <Code>{change.field}</Code>
      </span>
    ),
    before: <HistoryValueCell value={change.old} t={t} />,
    after: <HistoryValueCell value={change.new} t={t} />,
    changed: true,
  }));
  const revertable = onRevert !== undefined && entry.can_revert;
  if (!rows.length && !entry.statements.length && !entry.merge && !revertable) return null;
  const revertAction: ActionDescriptor = {
    id: "revert",
    label: t("history.revert"),
    placement: "toolbar",
    danger: true,
    confirm: { title: t("history.revertConfirm.title"), body: t("history.revertConfirm.body"), danger: true },
    run: async () => {
      await onRevert?.(entry);
    },
  };
  return (
    <CollapsibleRoot variant="flush">
      <CollapsibleTrigger>
        <CollapsibleIcon />
        {t("history.details")}
      </CollapsibleTrigger>
      <CollapsiblePanel>
        <div className="grid min-w-0 gap-3 pt-1">
          {rows.length ? (
            <ComparisonRows
              rows={rows}
              fieldLabel={t("history.field")}
              beforeLabel={t("history.before")}
              afterLabel={t("history.after")}
            />
          ) : null}
          {entry.statements.length ? <StatementVersions statements={entry.statements} record={record} t={t} /> : null}
          {entry.merge ? <MergeFacts merge={entry.merge} t={t} /> : null}
          {revertable ? <RecordActionBar record={{ id: record.id }} actions={[revertAction]} blocked={reverting} /> : null}
        </div>
      </CollapsiblePanel>
    </CollapsibleRoot>
  );
}

function StatementVersions({ statements, record, t }: {
  statements: readonly HistoryStatement[];
  record: RecordHistory;
  t: ProvenanceT;
}): ReactElement {
  return (
    <section className="grid gap-1" aria-label={t("history.statementsTitle")}>
      <h4 className="text-13 font-semibold text-fg">{t("history.statementsTitle")}</h4>
      <ul className="grid gap-1">
        {statements.map((statement, index) => (
          <li key={`${statement.id}:${index}`} className="flex min-w-0 flex-wrap items-center gap-1.5 text-13">
            <Badge tone={statementVerb(statement) === "withdrawn" ? "warning" : "neutral"}>
              {statementVerbLabel(t, statementVerb(statement))}
            </Badge>
            <span className="font-medium text-fg">{statement.aspect}</span>
            <Chip size="sm" tone="info">{titleCase(optionToken(statement.source_kind))}</Chip>
            {statement.source ? <StatementReference reference={statement.source} record={record} />
              : <span className="text-fg-muted">{statement.asserted_by}</span>}
            <span className="text-fg-muted">{t("history.statement.confidence", { confidence: statement.confidence })}</span>
            <Code truncate>{JSON.stringify(statement.values)}</Code>
            {statement.evidence.map((reference) => <StatementReference key={`${reference.model}:${reference.id}:${reference.pointer}`}
              reference={reference} record={record} />)}
          </li>
        ))}
      </ul>
    </section>
  );
}

function StatementReference({ reference, record }: {
  reference: HistoryStatement["evidence"][number];
  record: RecordHistory;
}): ReactElement {
  const sourceLink = reference.model.toLowerCase() === "integrate.recordlink";
  return <span className="text-fg-muted"><RecordReference
    model={sourceLink ? record.model : reference.model}
    id={sourceLink ? record.id : reference.id}
    label={reference.label}
    search={sourceLink ? { chatterTab: "integrate.sources", source: reference.id } : undefined}
  />{reference.pointer ? <Code>{reference.pointer}</Code> : null}</span>;
}

function MergeFacts({ merge, t }: { merge: NonNullable<HistoryEntry["merge"]>; t: ProvenanceT }): ReactElement {
  const chip = (record: NonNullable<HistoryEntry["merge"]>["absorbed"][number], note?: ReactNode): ReactElement => (
    <Chip key={`${record.model}:${record.id}`} size="sm" tone={record.retained ? "info" : "warning"}>
      <RecordReference model={record.model} id={record.id} />
      {note ? <span className="text-fg-muted"> · {note}</span> : null}
    </Chip>
  );
  return (
    <section className="grid gap-1 text-13" aria-label={t("history.mergeTitle")}>
      <h4 className="font-semibold text-fg">{t("history.mergeTitle")}</h4>
      <p className="flex flex-wrap items-center gap-1.5">
        <span className="text-fg-muted">{t("history.survivor")}</span>
        {chip(merge.survivor)}
      </p>
      <p className="flex flex-wrap items-center gap-1.5">
        <span className="text-fg-muted">{t("history.absorbed")}</span>
        {merge.absorbed.map((record) => chip(record, record.retained ? t("history.redirects") : t("history.removed")))}
      </p>
    </section>
  );
}


/** The changeset action's label; every key is named here so the bundle liveness rule sees it. */
function actionLabel(t: ProvenanceT, action: string | null | undefined): string {
  switch (optionToken(action)) {
    case "create":
      return t("history.action.create");
    case "update":
      return t("history.action.update");
    case "delete":
      return t("history.action.delete");
    default:
      return optionToken(action);
  }
}

/** The changeset activity's label, keyed statically for the same reason. */
function activityLabel(t: ProvenanceT, activity: string | null | undefined): string {
  switch (optionToken(activity)) {
    case "manual":
      return t("history.activity.manual");
    case "pull":
      return t("history.activity.pull");
    case "push":
      return t("history.activity.push");
    case "import":
      return t("history.activity.import");
    case "merge":
      return t("history.activity.merge");
    case "revert":
      return t("history.activity.revert");
    case "seed":
      return t("history.activity.seed");
    case "system":
      return t("history.activity.system");
    case "run":
      return t("history.activity.run");
    default:
      return optionToken(activity);
  }
}

/** A statement version's verb label (asserted, applied, withdrawn, quarantined, deprecated). */
function statementVerbLabel(t: ProvenanceT, verb: string): string {
  switch (verb) {
    case "asserted":
      return t("history.statement.asserted");
    case "applied":
      return t("history.statement.applied");
    case "withdrawn":
      return t("history.statement.withdrawn");
    case "quarantined":
      return t("history.statement.quarantined");
    case "deprecated":
      return t("history.statement.deprecated");
    default:
      return verb;
  }
}
