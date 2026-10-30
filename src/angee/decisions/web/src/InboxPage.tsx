import { useAuthoredQuery } from "@angee/refine";
import { rowValueAtPath, type Row } from "@angee/metadata";
import type { ReactElement } from "react";
import {
  AbsoluteTime, Column, ErrorBanner, Form, List, LoadingPanel, RecordReference, ResourceList, StatusBadge, useRuntimeAuth,
  type RecordPanelContext, type RecordToolbarContext, type StringIdRow,
} from "@angee/ui";

import { DecisionCard } from "./DecisionCard";
import { DECISION_MODEL, DECISION_MODELS, DecisionDocument } from "./documents.console";
import { useDecisionsT } from "./i18n";

const OPEN_TONE = "needs-decision";

/** Inbox presets and record chrome compose the same card used by record timelines. */
export function InboxPage(): ReactElement {
  const t = useDecisionsT();
  const { user } = useRuntimeAuth();
  if (!user) return <LoadingPanel />;
  return <ResourceList<StringIdRow> resource={DECISION_MODEL} placement="inline" routed hideCreate
    filterOptions={[
      { id: "assigned", label: t("inbox.assigned"), group: t("inbox.scope"), filter: { assignees: { exact: user.id } } },
      { id: "can_act", label: t("inbox.canAct"), group: t("inbox.scope"), filter: { can_act: { exact: true } } },
      { id: "requested", label: t("inbox.requested"), group: t("inbox.scope"), filter: { requester: { exact: user.id } } },
      { id: "open", label: t("inbox.open"), group: t("inbox.state"), filter: { is_open: { exact: true } } },
      { id: "answered", label: t("inbox.answered"), group: t("inbox.state"), filter: { is_open: { exact: false } } },
    ]}>
    <List resource={DECISION_MODEL} order={{ created_at: "DESC" }} emptyContent={t("inbox.empty")}>
      <Column field="kind_label" header={t("inbox.question")} />
      {/* The records the question concerns: the first by name, the rest as a count. */}
      <Column field="records" header={t("inbox.concerns")} sortable={false}
        selectionPaths={["records.record_model", "records.record_id"]} render={(row) => <ConcernsCell row={row} />} />
      <Column field="created_at" header={t("inbox.asked")} />
      <Column field="verdict_label" header={t("inbox.answer")} render={(row) => row.is_open === false && typeof row.verdict_label === "string" && row.verdict_label
        ? row.verdict_label : <StatusBadge value={OPEN_TONE}>{t("decision.open")}</StatusBadge>} selectionPaths={["is_open"]} />
      <Column field="answered_by.display_name" header={t("inbox.answeredBy")} />
    </List>
    <Form resource={DECISION_MODEL} readOnly eyebrow={t("decision.eyebrow")}
      returning={["is_open", "answered_at", "created_at", "answered_by.display_name"]}
      contextLine={(context) => <DecisionFactsLine {...context} />}
      formExtras={({ record }) => typeof record?.id === "string"
        ? <DecisionDetails recordId={record.id} /> : null}>
    </Form>
  </ResourceList>;
}

function ConcernsCell({ row }: { row: Row }): ReactElement | null {
  const t = useDecisionsT();
  const value = rowValueAtPath(row, "records");
  const records = Array.isArray(value) ? value.flatMap((item) => item && typeof item === "object"
    && typeof (item as { record_model?: unknown }).record_model === "string" && typeof (item as { record_id?: unknown }).record_id === "string"
    ? [item as { record_model: string; record_id: string }] : []) : [];
  const [first, ...rest] = records;
  if (!first) return null;
  return <span className="inline-flex min-w-0 items-baseline gap-1">
    <RecordReference model={first.record_model} id={first.record_id} />
    {rest.length ? <span className="text-fg-muted">{t("inbox.more", { count: rest.length })}</span> : null}
  </span>;
}

/** The facts line under the question: when it was asked, when and by whom it was answered. */
function DecisionFactsLine({ record }: RecordToolbarContext): ReactElement | null {
  const t = useDecisionsT();
  if (!record) return null;
  const answeredBy = rowValueAtPath(record, "answered_by.display_name");
  return <>
    {t("decision.asked")}<AbsoluteTime value={record.created_at as string} display="datetime" />
    {record.is_open === false && record.answered_at ? <>{" · "}{t("decision.answered", { name: typeof answeredBy === "string" ? answeredBy : t("decision.operator") })}
      <AbsoluteTime value={record.answered_at as string} display="datetime" /></> : <>{" · "}{t("decision.open")}</>}
  </>;
}

function DecisionDetails({ recordId }: Pick<RecordPanelContext, "recordId">): ReactElement {
  const t = useDecisionsT();
  const query = useAuthoredQuery(DecisionDocument, { id: recordId }, { models: DECISION_MODELS });
  if (query.isLoading) return <LoadingPanel />;
  const decision = query.data?.decisions_by_pk;
  if (!decision) return <ErrorBanner description={t("decision.unavailable")} />;
  return <DecisionCard decision={decision} detail />;
}
