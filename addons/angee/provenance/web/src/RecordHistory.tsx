import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredQuery } from "@angee/refine";
import { Chip, Button, Code, CodeBlock, ComparisonRows, EmptyState, ErrorBanner, LoadingPanel, PaneRow, PaneRows, PaneToolbar, RecordActionBar, RecordReference, formatDateTime, optionToken, titleCase, useActionResultMutation, type ActionDescriptor, type ComparisonRow } from "@angee/ui";
import { useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";

import {
  RecordHistoryDocument,
  type HistoryEntry,
  type HistoryStatement,
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
  /** One line per changeset, no details. */
  compact?: boolean;
  /** The changeset a Timeline line opened: shown with its details and scrolled into view. */
  selected?: string;
}

/** The timeline with its read, paging and the Revert verb bound to one record. */
export function RecordHistoryTimeline({
  model,
  id,
  dataProviderName,
  compact = false,
  selected,
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
      selected={selected}
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
  selected?: string;
  fetching?: boolean;
  offset?: number;
  onPage?: (offset: number) => void;
  onRevert?: (entry: HistoryEntry) => Promise<void> | void;
  reverting?: boolean;
}

/** Presentation over one loaded page: one pane row per changeset, the opened one with its details below. */
export function HistoryTimeline({
  history,
  compact = false,
  selected,
  fetching = false,
  offset = 0,
  onPage,
  onRevert,
  reverting = false,
}: HistoryTimelineProps): ReactElement {
  const t = useProvenanceT();
  const [openId, setOpenId] = useState<string | null>(selected ?? null);
  useEffect(() => {
    if (selected) setOpenId(selected);
  }, [selected]);
  const pager = onPage && history.total > HISTORY_PAGE_SIZE ? (
    <li className="flex items-center justify-center gap-2 py-2">
      <Button size="sm" variant="ghost" disabled={offset === 0 || fetching}
        onClick={() => onPage(Math.max(0, offset - HISTORY_PAGE_SIZE))}>
        {t("history.newer")}
      </Button>
      <Button size="sm" variant="ghost" disabled={offset + HISTORY_PAGE_SIZE >= history.total || fetching}
        onClick={() => onPage(offset + HISTORY_PAGE_SIZE)}>
        {t("history.older")}
      </Button>
    </li>
  ) : null;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneToolbar />
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        <PaneRows>
          {history.entries.map((entry) => (
            <HistoryRow
              key={entry.id}
              entry={entry}
              record={history}
              open={!compact && openId === entry.id}
              focused={selected === entry.id}
              onToggle={compact ? undefined : () => setOpenId((current) => current === entry.id ? null : entry.id)}
              onRevert={onRevert}
              reverting={reverting}
              t={t}
            />
          ))}
          {pager}
        </PaneRows>
      </div>
    </div>
  );
}

function HistoryRow({ entry, record, open, focused, onToggle, onRevert, reverting, t }: {
  entry: HistoryEntry;
  record: RecordHistory;
  open: boolean;
  focused: boolean;
  onToggle?: () => void;
  onRevert: HistoryTimelineProps["onRevert"];
  reverting: boolean;
  t: ProvenanceT;
}): ReactElement {
  const ref = useRef<HTMLLIElement>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "center" });
  }, [focused]);
  const counts = [
    entry.changes.length ? t("history.changes", { count: entry.changes.length }) : null,
    entry.statements.length ? t("history.statements", { count: entry.statements.length }) : null,
  ].filter((part): part is string => part !== null);
  const failed = optionToken(entry.outcome) === "failure";
  return (
    <li ref={ref}>
      <PaneRow
        icon={failed ? "triangle-alert" : "pencil"}
        tone={failed ? "danger" : entryTone(entry)}
        title={actionLabel(t, entry.action)}
        detail={[counts.join(" · "), failed ? t("history.outcome.failure") : null].filter(Boolean).join(" · ") || undefined}
        meta={<time dateTime={entry.recorded_at}>{formatDateTime(entry.recorded_at)}</time>}
        sub={[entry.actor ?? t("history.system"), activityLabel(t, entry.activity), entry.source_kind ? titleCase(optionToken(entry.source_kind)) : null, entry.change_reason]
          .filter(Boolean).join(" · ")}
        verb={onToggle ? t("history.details") : undefined}
        selected={open}
        onOpen={onToggle}
      />
      {open ? (
        <div className="ml-6 pb-2 pr-1">
          <EntryDetails entry={entry} record={record} onRevert={onRevert} reverting={reverting} t={t} />
        </div>
      ) : null}
    </li>
  );
}

function HistoryValueCell({ value, t }: { value: unknown; t: ProvenanceT }): ReactElement {
  const rendered = historyValue(value);
  if (rendered.kind === "empty") return <span className="text-fg-subtle">{t("history.empty")}</span>;
  if (rendered.kind === "json") return <CodeBlock>{rendered.text}</CodeBlock>;
  return <span className="[overflow-wrap:anywhere]">{rendered.text}</span>;
}

/** Field deltas, statement versions, merge facts and the Revert verb, under the opened row. */
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
            <Chip tone={statementVerb(statement) === "withdrawn" ? "warning" : "neutral"}>
              {statementVerbLabel(t, statementVerb(statement))}
            </Chip>
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
