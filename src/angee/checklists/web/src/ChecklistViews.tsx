import * as React from "react";
import { rowPublicId, type Row } from "@angee/metadata";
import {
  Alert, Chip, CountBadge, DefinitionPair, ErrorBanner, InlineEmpty, LoadingPanel, RecordReference, RowsListView,
  StatusBadge, StatusIconGroup, defaultWidgets, useStatusTone,
  type ListColumn, type StatusToneMap,
} from "@angee/ui";

import type { ChecklistFields } from "./documents.console";
import { useChecklistsT } from "./i18n";

/** The item outcomes, in the order the legend and counts show them. */
export const CHECKLIST_STATUSES = ["passed", "risk", "conflict", "unresolved", "not_checked", "not_applicable"] as const;
export type ChecklistStatus = typeof CHECKLIST_STATUSES[number];
/** The statuses a record's summary takes; a summary is never not applicable. */
export const CHECKLIST_ASSESSMENTS = ["passed", "risk", "conflict", "unresolved", "not_checked"] as const;
export type ChecklistAssessment = typeof CHECKLIST_ASSESSMENTS[number];
/** The statuses needing attention, strongest first. */
export const CHECKLIST_ATTENTION = ["conflict", "risk", "unresolved"] as const;

/** Checklists owns the status vocabulary; every badge, tile and chart reads the composed tone registry. */
export const checklistTones = {
  "checklist-passed": "success",
  "checklist-risk": "warning",
  "checklist-conflict": "danger",
  "checklist-unresolved": "muted",
  "checklist-not_applicable": "muted",
  "checklist-not_checked": "muted",
} satisfies StatusToneMap;

export function checklistToneKey(status: string): string { return `checklist-${status}`; }

/** The selection a list column or record read needs from a node's `checklist`. */
export const CHECKLIST_FIELDS: readonly string[] = [
  ...["status", "detail", "complete", "stale", "recorded_at"].map((field) => `checklist.${field}`),
  ...["key", "label", "status", "detail", "item_keys"].map((field) => `checklist.groups.${field}`),
  ...["key", "label", "category", "blocking", "status", "detail", "stale", "recorded_at", "evidence.model", "evidence.id"]
    .map((field) => `checklist.items.${field}`),
];

/** Each status's label key: the type requires one for every status, and each key is a literal. */
const CHECKLIST_STATUS_LABELS = {
  passed: "status.passed",
  risk: "status.risk",
  conflict: "status.conflict",
  unresolved: "status.unresolved",
  not_checked: "status.not_checked",
  not_applicable: "status.not_applicable",
} as const satisfies Record<ChecklistStatus, string>;

function isKnownStatus(status: string): status is ChecklistStatus {
  return (CHECKLIST_STATUSES as readonly string[]).includes(status);
}

export function useChecklistStatusLabel(): (status: string) => string {
  const t = useChecklistsT();
  return React.useCallback(
    (status: string) => t(isKnownStatus(status) ? CHECKLIST_STATUS_LABELS[status] : "status.unknown"),
    [t],
  );
}

type ChecklistItem = ChecklistFields["items"][number];
type ChecklistGroup = ChecklistFields["groups"][number];
type ItemRow = ChecklistItem & { id: string };

function isEvidence(value: unknown): boolean {
  return !!value && typeof value === "object"
    && "model" in value && typeof value.model === "string" && "id" in value && typeof value.id === "string";
}

function isItem(value: unknown): value is ChecklistItem {
  return !!value && typeof value === "object"
    && "key" in value && typeof value.key === "string"
    && "label" in value && typeof value.label === "string"
    && "category" in value && (value.category === null || typeof value.category === "string")
    && "blocking" in value && typeof value.blocking === "boolean"
    && "status" in value && typeof value.status === "string"
    && "detail" in value && typeof value.detail === "string"
    && "stale" in value && typeof value.stale === "boolean"
    && "evidence" in value && Array.isArray(value.evidence) && value.evidence.every(isEvidence);
}

function isGroup(value: unknown): value is ChecklistGroup {
  return !!value && typeof value === "object"
    && "key" in value && typeof value.key === "string"
    && "label" in value && typeof value.label === "string"
    && "status" in value && typeof value.status === "string"
    && "detail" in value && typeof value.detail === "string"
    && "item_keys" in value && Array.isArray(value.item_keys) && value.item_keys.every((key) => typeof key === "string");
}

/** Resource rows are dynamic: read a row's `checklist` at their boundary, failing closed instead of inferring a pass. */
export function checklistFromRow(row: Row | null | undefined): ChecklistFields | null {
  const value = row?.checklist;
  if (!value || typeof value !== "object"
    || !("status" in value) || typeof value.status !== "string"
    || !("detail" in value) || typeof value.detail !== "string"
    || !("complete" in value) || typeof value.complete !== "boolean"
    || !("stale" in value) || typeof value.stale !== "boolean"
    || !("recorded_at" in value) || (value.recorded_at !== null && typeof value.recorded_at !== "string")
    || !("groups" in value) || !Array.isArray(value.groups) || !value.groups.every(isGroup)
    || !("items" in value) || !Array.isArray(value.items) || !value.items.every(isItem)) return null;
  return value as ChecklistFields;
}

export function ChecklistStatusBadge({ status }: { status: string }): React.ReactElement {
  const label = useChecklistStatusLabel();
  return <StatusBadge value={checklistToneKey(status)}>{label(status)}</StatusBadge>;
}

export function ChecklistLegend({ statuses = [...CHECKLIST_ASSESSMENTS, "not_applicable"] }: {
  statuses?: readonly string[];
}): React.ReactElement {
  const t = useChecklistsT();
  return <div className="flex flex-wrap items-center gap-x-4 gap-y-1" aria-label={t("checks")}>
    {statuses.map((status) => <ChecklistStatusBadge key={status} status={status} />)}
  </div>;
}

const DateTimeRead = defaultWidgets.datetime.read;

export interface ChecklistSectionProps {
  /** The record's checklist; `null` once read and absent, `undefined` while unread. */
  checklist: ChecklistFields | null | undefined;
  isFetching?: boolean;
  error?: string | null;
  /** The owner's own guidance in the information notice, replacing the generic help. */
  help?: React.ReactNode;
}

/** One record's checklist: its summary, then each item with its result and readable evidence. */
export function ChecklistSection({ checklist, isFetching = false, error = null, help }: ChecklistSectionProps): React.ReactElement {
  const t = useChecklistsT();
  const columns = React.useMemo<readonly ListColumn<ItemRow>[]>(() => [
    { field: "label", header: t("checks"), sortable: false },
    { field: "status", header: t("status"), sortable: false, render: (item) => <ChecklistStatusBadge status={item.status} /> },
    { field: "detail", header: t("result"), sortable: false },
    { field: "evidence", header: t("evidence"), sortable: false, interactive: true,
      render: (item) => <div className="flex flex-wrap gap-3">
        {item.evidence.map((ref) => <RecordReference key={`${ref.model}:${ref.id}`} model={ref.model} id={ref.id}
          label={t("recordedBy")} />)}
      </div> },
  ], [t]);
  const rows = React.useMemo(() => checklist?.items.map((item) => ({ ...item, id: item.key })) ?? [], [checklist]);
  return <div className="space-y-4">
    {error ? <ErrorBanner description={error} /> : null}
    {isFetching && !checklist ? <LoadingPanel loadingLabel={t("loading")} /> : null}
    <Alert tone={checklist?.stale ? "warning" : "info"} title={t(checklist?.stale ? "changed" : "title")}>
      {checklist?.stale ? t("changedHelp") : help ?? t("help")}
    </Alert>
    {checklist ? <>
      <div className="flex flex-wrap items-center gap-3">
        <ChecklistStatusBadge status={checklist.status} />
        {checklist.recorded_at ? <DateTimeRead value={checklist.recorded_at} /> : null}
        {!checklist.complete ? <Chip>{t("incomplete")}</Chip> : null}
      </div>
      <RowsListView rows={rows} columns={columns} scope="local" presentation="embedded" emptyContent={t("noResults")} />
    </> : <InlineEmpty label={t("noResults")} />}
  </div>;
}

export interface ChecklistColumnOptions {
  /** The record's model label and the tab its checklist section occupies, for a cell's link. */
  recordModel: string;
  tab?: string;
}

function RecordLink({ row, options, label }: { row: Row; options: ChecklistColumnOptions; label: React.ReactNode }) {
  const id = rowPublicId(row);
  return id ? <RecordReference model={options.recordModel} id={id} tab={options.tab} label={label} /> : <>{label}</>;
}

function ChecksHeading(): React.ReactElement { return <>{useChecklistsT()("checks")}</>; }

/** One cell counting each item status, with stale and incomplete flags. */
export function checklistItemsColumn(options: ChecklistColumnOptions): ListColumn {
  return {
    field: "checklist.items", header: <ChecksHeading />, sortable: false, interactive: true,
    selectionPaths: CHECKLIST_FIELDS,
    render: (row) => <ChecklistItemsCell row={row} options={options} />,
  };
}

function ChecklistItemsCell({ row, options }: { row: Row; options: ChecklistColumnOptions }): React.ReactElement {
  const t = useChecklistsT();
  const label = useChecklistStatusLabel();
  const statusTone = useStatusTone();
  const checklist = checklistFromRow(row);
  const incomplete = Boolean(checklist && !checklist.complete && checklist.status !== "not_checked");
  const counts = new Map<string, number>();
  for (const item of checklist?.items ?? []) {
    const status = isKnownStatus(item.status) ? item.status : "unknown";
    counts.set(status, (counts.get(status) ?? 0) + 1);
  }
  const entries = [...CHECKLIST_STATUSES, "unknown"].flatMap((status) => {
    const count = counts.get(status);
    return count ? [{ status, count, label: label(status) }] : [];
  });
  const summaryLabel = [t("checks"), entries.length
    ? entries.map((entry) => `${entry.label}: ${entry.count}`).join(", ") : t("noResultsShort"),
  ...(checklist?.stale ? [t("stale")] : []), ...(incomplete ? [t("incomplete")] : [])].join(" · ");
  const summary = <span role="group" aria-label={summaryLabel} className="inline-flex flex-nowrap items-center gap-2 whitespace-nowrap">
    {entries.length ? entries.map((entry) => <span key={entry.status} className="inline-flex items-center gap-1">
      <CountBadge value={entry.count} tone={statusTone(checklistToneKey(entry.status), undefined, { unknownTone: "muted" })}
        aria-label={`${entry.label}: ${entry.count}`} />
      <span className="text-2xs text-fg-muted">{entry.label}</span>
    </span>) : <Chip>{t("noResultsShort")}</Chip>}
    {checklist?.stale ? <Chip tone="warning">{t("stale")}</Chip> : null}
    {incomplete ? <Chip>{t("incomplete")}</Chip> : null}
  </span>;
  return <RecordLink row={row} options={options} label={summary} />;
}

function CategoriesHeading(): React.ReactElement { return <>{useChecklistsT()("categories")}</>; }

/** One cell showing each category's status as an icon, in the server's order and with its labels. */
export function checklistCategoriesColumn(): ListColumn {
  return {
    id: "checklist-categories", field: "checklist.groups", header: <CategoriesHeading />,
    sortable: false, minWidth: 128, selectionPaths: CHECKLIST_FIELDS,
    render: (row) => <ChecklistCategoriesCell row={row} />,
  };
}

function ChecklistCategoriesCell({ row }: { row: Row }): React.ReactElement {
  const t = useChecklistsT();
  const label = useChecklistStatusLabel();
  const statusTone = useStatusTone();
  const groups = checklistFromRow(row)?.groups ?? [];
  if (!groups.length) return <Chip>{t("noResultsShort")}</Chip>;
  return <StatusIconGroup label={t("categories")} items={groups.map((group) => {
    const tone = statusTone(checklistToneKey(group.status), undefined, { unknownTone: "muted" });
    return {
      key: group.key, label: `${group.label}: ${label(group.status)}`, description: group.detail,
      tone: tone === "success" || tone === "warning" || tone === "danger" ? tone : "muted",
      icon: group.status === "not_applicable" || group.status === "not_checked" ? "minus" : undefined,
    };
  })} />;
}

/** The record's summary status with its detail. */
export function checklistStatusColumn(options: ChecklistColumnOptions & { header: React.ReactNode }): ListColumn {
  return {
    field: "checklist.status", header: options.header, sortable: false, selectionPaths: CHECKLIST_FIELDS,
    interactive: true, minWidth: 180,
    render: (row) => <ChecklistStatusCell row={row} options={options} />,
  };
}

function ChecklistStatusCell({ row, options }: { row: Row; options: ChecklistColumnOptions }): React.ReactElement {
  const t = useChecklistsT();
  const checklist = checklistFromRow(row);
  const detail = checklist?.detail ?? t("noResults");
  return <DefinitionPair as="div" orientation="stacked" density="cell"
    value={<RecordLink row={row} options={options} label={<ChecklistStatusBadge status={checklist?.status ?? "unresolved"} />} />}
    detail={<span title={detail}>{detail}</span>} />;
}
