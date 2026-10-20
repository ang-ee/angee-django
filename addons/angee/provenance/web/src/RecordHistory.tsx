import type { ActionFieldName } from "@angee/gql/console/actions";
import { useAuthoredQuery } from "@angee/refine";
import { useModelMetadata } from "@angee/metadata";
import { Chip, Code, CodeBlock, ComparisonRows, EmptyState, ErrorBanner, LoadingPanel, PaneRow, PaneRows, PaneToolbar, RecordActionBar, RecordReference, ResourcePaneFooter, SectionEyebrow, StatusBadge, formatDateTime, formatJson, optionLabel, optionToken, useActionResultMutation, useEnumOptions, useStatusTone, type ActionDescriptor, type ComparisonRow, type StatusIconTone, type WidgetOption } from "@angee/ui";
import { useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";

import {
  RecordHistoryDocument,
  type HistoryEntry,
  type HistoryStatement,
  type RecordHistory,
} from "./documents.console";
import { historyValue, statementVerb } from "./history-entries";
import { useProvenanceT, type ProvenanceT } from "./i18n";

/** Changesets per page; the server caps a page at 100. */
export const HISTORY_PAGE_SIZE = 25;
/** The changeset model whose enum options label a row's source kind. */
const CHANGESET_MODEL = "provenance.Changeset";

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
  const sourceKinds = useEnumOptions(CHANGESET_MODEL, "source_kind", { casing: "upper" });
  const statusTone = useStatusTone();
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
              sourceKinds={sourceKinds}
              statusTone={statusTone}
              t={t}
            />
          ))}
        </PaneRows>
      </div>
      {/* Offset paging over the changesets: the pane footer, as under every pane list. */}
      {onPage && history.total > HISTORY_PAGE_SIZE ? (
        <ResourcePaneFooter pager={{ page: Math.floor(offset / HISTORY_PAGE_SIZE) + 1, pageSize: HISTORY_PAGE_SIZE, total: history.total }}
          disabled={fetching} pagerTotalUnit={t("history.pagerUnit")} labelElement="span"
          onPageChange={(page) => onPage((page - 1) * HISTORY_PAGE_SIZE)} />
      ) : null}
    </div>
  );
}

function HistoryRow({ entry, record, open, focused, onToggle, onRevert, reverting, sourceKinds, statusTone, t }: {
  entry: HistoryEntry;
  record: RecordHistory;
  open: boolean;
  focused: boolean;
  onToggle?: () => void;
  onRevert: HistoryTimelineProps["onRevert"];
  reverting: boolean;
  sourceKinds: readonly WidgetOption[];
  statusTone: ReturnType<typeof useStatusTone>;
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
        // The composed status vocabulary tones the activity; a failed outcome overrides it.
        tone={rowTone(statusTone(failed ? "failure" : entry.activity))}
        title={actionLabel(t, entry.action)}
        detail={[counts.join(" · "), failed ? t("history.outcome.failure") : null].filter(Boolean).join(" · ") || undefined}
        meta={<time dateTime={entry.recorded_at}>{formatDateTime(entry.recorded_at)}</time>}
        sub={[entry.actor ?? t("history.system"), activityLabel(t, entry.activity), entry.source_kind ? optionLabel(sourceKinds, entry.source_kind) : null, entry.change_reason]
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
        <FieldName model={change.model} field={change.field} />
      </span>
    ),
    cells: {
      before: <HistoryValueCell value={change.old} t={t} />,
      after: <HistoryValueCell value={change.new} t={t} />,
    },
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
          columns={[{ key: "before", label: t("history.before") }, { key: "after", label: t("history.after") }]}
        />
      ) : null}
      {entry.statements.length ? <StatementVersions statements={entry.statements} record={record} t={t} /> : null}
      {entry.merge ? <MergeFacts merge={entry.merge} t={t} /> : null}
      {revertable ? <RecordActionBar record={{ id: record.id }} actions={[revertAction]} blocked={reverting} /> : null}
    </div>
  );
}

/** The pane row's glyph tone from a status tone: the feedback intents, else muted. */
function rowTone(tone: ReturnType<ReturnType<typeof useStatusTone>>): StatusIconTone {
  return tone === "info" || tone === "success" || tone === "warning" || tone === "danger" ? tone : "muted";
}

/** A field's authored label; the wire name only when the model's metadata is not loaded. */
function FieldName({ model, field }: { model: string; field: string }): ReactElement {
  const metadata = useModelMetadata(model);
  const label = metadata?.fields[field]?.label;
  return label ? <span className="font-medium text-fg">{label}</span> : <Code>{field}</Code>;
}

function StatementVersions({ statements, record, t }: {
  statements: readonly HistoryStatement[];
  record: RecordHistory;
  t: ProvenanceT;
}): ReactElement {
  const sourceKinds = useEnumOptions(CHANGESET_MODEL, "source_kind", { casing: "upper" });
  return (
    <section className="grid gap-1" aria-label={t("history.statementsTitle")}>
      <SectionEyebrow>{t("history.statementsTitle")}</SectionEyebrow>
      <PaneRows>
        {statements.map((statement, index) => {
          const verb = statementVerb(statement);
          return (
            <li key={`${statement.id}:${index}`}>
              <PaneRow
                icon="pencil"
                tone={verb === "withdrawn" ? "warning" : "muted"}
                title={<FieldName model={record.model} field={statement.aspect} />}
                detail={statementVerbLabel(t, verb)}
                meta={t("history.statement.confidence", { confidence: statement.confidence })}
                sub={<>
                  <StatusBadge value={statement.source_kind} options={sourceKinds} display="text" />
                  {" · "}
                  {statement.source ? <StatementReference reference={statement.source} record={record} /> : statement.asserted_by}
                  {" · "}
                  <Code truncate>{formatJson(statement.values, { compact: true })}</Code>
                  {statement.evidence.map((reference) => <span key={`${reference.model}:${reference.id}:${reference.pointer}`}> · <StatementReference reference={reference} record={record} /></span>)}
                </>}
              />
            </li>
          );
        })}
      </PaneRows>
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
  /></span>;
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
      <SectionEyebrow>{t("history.mergeTitle")}</SectionEyebrow>
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
      return t("history.action.other");
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
      return t("history.activity.other");
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
      return t("history.statement.other");
  }
}
