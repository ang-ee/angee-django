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
  optionToken,
  titleCase,
  useActionResultMutation,
  useRecordChromeContext,
  type ActionDescriptor,
  type ComparisonRow,
  type StepListItem,
} from "@angee/ui";
import { useMemo, useState, type ReactElement, type ReactNode } from "react";

import {
  RecordHistoryDocument,
  type HistoryEntry,
  type HistoryStatement,
  type RecordHistory,
} from "./documents.console";
import { entryTone, historyValue, statementVerb } from "./history-entries";
import { useProvenanceT, type ProvenanceT } from "./i18n";

/** The History tab's id; `?recordTab=history` opens it on any record. */
export const HISTORY_TAB_ID = "history";
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
  /** One line per changeset linking to the History tab: the chatter rendering. */
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
      : <ErrorBanner title={t("history.unavailable")} />;
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

/** The saved-record tab body: the open record's own history. */
export function HistoryPane(): ReactElement {
  const { resource, recordId, dataProviderName } = useRecordChromeContext();
  return <RecordHistoryTimeline model={resource} id={recordId} dataProviderName={dataProviderName} />;
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
      ? <RecordReference model={history.model} id={history.id} tab={HISTORY_TAB_ID} label={t("history.openTab")} />
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

/** "<actor> <activity> · <action>": who wrote, how the write came about, what it did. */
function EntryTitle({ entry, t }: { entry: HistoryEntry; t: ProvenanceT }): ReactElement {
  const actor = entry.actor_model && entry.actor_id
    ? <RecordReference model={entry.actor_model} id={entry.actor_id} />
    : <span>{entry.actor ?? t("history.system")}</span>;
  return (
    <span className="inline-flex min-w-0 flex-wrap items-center gap-1.5">
      {actor}
      <span className="font-normal text-fg-muted">{t(`history.activity.${optionToken(entry.activity)}`)}</span>
      <Badge tone="neutral">{t(`history.action.${optionToken(entry.action)}`)}</Badge>
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
    key: change.field,
    label: <Code>{change.field}</Code>,
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
          {entry.statements.length ? <StatementVersions statements={entry.statements} t={t} /> : null}
          {entry.merge ? <MergeFacts merge={entry.merge} t={t} /> : null}
          {revertable ? <RecordActionBar record={{ id: record.id }} actions={[revertAction]} blocked={reverting} /> : null}
        </div>
      </CollapsiblePanel>
    </CollapsibleRoot>
  );
}

function StatementVersions({ statements, t }: { statements: readonly HistoryStatement[]; t: ProvenanceT }): ReactElement {
  return (
    <section className="grid gap-1" aria-label={t("history.statementsTitle")}>
      <h4 className="text-13 font-semibold text-fg">{t("history.statementsTitle")}</h4>
      <ul className="grid gap-1">
        {statements.map((statement, index) => (
          <li key={`${statement.id}:${index}`} className="flex min-w-0 flex-wrap items-center gap-1.5 text-13">
            <Badge tone={statementVerb(statement) === "withdrawn" ? "warning" : "neutral"}>
              {t(`history.statement.${statementVerb(statement)}`)}
            </Badge>
            <span className="font-medium text-fg">{statement.aspect}</span>
            <Chip size="sm" tone="info">{titleCase(optionToken(statement.source_kind))}</Chip>
            <span className="text-fg-muted">{statement.asserted_by}</span>
            <span className="text-fg-muted">{t("history.statement.confidence", { confidence: statement.confidence })}</span>
            <Code truncate>{JSON.stringify(statement.values)}</Code>
          </li>
        ))}
      </ul>
    </section>
  );
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
