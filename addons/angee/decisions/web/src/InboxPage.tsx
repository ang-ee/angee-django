import { useAuthoredQuery } from "@angee/refine";
import { rowValueAtPath } from "@angee/metadata";
import type { ReactElement } from "react";
import { Column, Field, Form, Group, List, LoadingPanel, ErrorBanner, ResourceList, useRuntimeAuth, type RecordPanelContext, type StringIdRow } from "@angee/ui";

import { DecisionCard } from "./DecisionCard";
import { DECISION_MODEL, DECISION_MODELS, DecisionDocument } from "./documents.console";
import { useDecisionsT } from "./i18n";

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
      <Column field="kind_label" header={t("inbox.kind")} />
      <Column field="requester.display_name" header={t("inbox.requester")} />
      <Column field="verdict" header={t("inbox.verdict")} />
    </List>
    <Form resource={DECISION_MODEL} readOnly
      formExtras={({ record, form }) => typeof record?.id === "string"
        ? <DecisionDetails recordId={record.id} refresh={form.reload} /> : null}>
      <Field name="kind_label" title />
      <Group label={t("decision.title")} columns={2}>
        <Field name="requester.display_name" label={t("decision.requester")}
          showWhen={(row) => Boolean(rowValueAtPath(row, "requester.display_name"))} />
        <Field name="answered_by.display_name" label={t("decision.answeredBy")}
          showWhen={(row) => Boolean(rowValueAtPath(row, "answered_by.display_name"))} />
        <Field name="answered_at" label={t("decision.answeredAt")} showWhen={(row) => Boolean(row.answered_at)} />
      </Group>
    </Form>
  </ResourceList>;
}

function DecisionDetails({ recordId, refresh }: Pick<RecordPanelContext, "recordId"> & { refresh: () => Promise<unknown> }): ReactElement {
  const t = useDecisionsT();
  const query = useAuthoredQuery(DecisionDocument, { id: recordId }, { models: DECISION_MODELS });
  if (query.isLoading) return <LoadingPanel />;
  const decision = query.data?.decisions_by_pk;
  if (!decision) return <ErrorBanner description={t("decision.unavailable")} />;
  return <DecisionCard decision={decision} onAnswered={async () => { await query.refetch(); await refresh(); }} />;
}
