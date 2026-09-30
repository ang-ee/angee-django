import type { ActionFieldName } from "@angee/gql/console/actions";
import { rowValueAtPath } from "@angee/metadata";
import {
  Action, Column, Facet, Field, Form, Group, List, RecordReference, ResourceList,
  useRecordActionMutation, useRouteHref, type ResourceListProps, type StringIdRow,
} from "@angee/ui";
import { RUN_MODEL, RUN_MODELS } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { StepRuns } from "./StepRuns";

export function RunsPage() {
  return <RunsList />;
}

/** One collection declaration for the routed page and all contextual run lists. */
export function RunsList({ baseFilter, embedded = false }: {
  baseFilter?: ResourceListProps["baseFilter"]; embedded?: boolean;
}) {
  const t = useWorkflowsT();
  const href = useRouteHref();
  const [cancel] = useRecordActionMutation<ActionFieldName>("cancel_workflow_run", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const [reprocess] = useRecordActionMutation<ActionFieldName>("reprocess_workflow_run", {
    dataProviderName: "console", invalidateModels: RUN_MODELS, linkTo: RUN_MODEL,
  });
  return <ResourceList<StringIdRow> resource={RUN_MODEL} hideCreate baseFilter={baseFilter}
    placement="inline" routed={!embedded} presentation={embedded ? "embedded" : undefined}
    rowHref={embedded ? (row) => href("workflows.runs.record", { id: row.id }) : undefined}
    fields={["subject_model"]} recordTabs={[
      { id: "steps", label: t("run.steps"), render: ({ recordId }) => <StepRuns runId={recordId} /> },
      { id: "children", label: t("run.children"), render: ({ recordId }) =>
        <RunsList embedded baseFilter={{ "parent_step.run": { exact: recordId } }} /> },
      { id: "evidence", label: t("run.evidence"), render: ({ recordId }) =>
        <List resource="workflows.WorkflowRunEvidence" scope="local" presentation="embedded"
          baseFilter={{ run: { exact: recordId } }} fields={["record_model"]} emptyContent={t("run.noEvidence")}>
          <Column field="record_id" header={t("run.evidence")} render={(row) =>
            typeof row.record_model === "string" && typeof row.record_id === "string"
              ? <RecordReference model={row.record_model} id={row.record_id} /> : t("run.redactedEvidence")} />
        </List> },
    ]}>
    <List order={{ created_at: "DESC" }} defaultGroups={{ list: { field: "status" }, board: { field: "status" } }}
      emptyContent={t("runs.empty")}>
      <Facet field="version.workflow" label={t("run.workflow")} />
      <Facet field="origin" label={t("run.origin")} />
      <Column field="status" header={t("run.status")} widget="statusBadge" />
      <Column field="version.workflow.name" header={t("run.workflow")} />
      <Column field="subject_id" header={t("run.subject")} render={(row) => typeof row.subject_id === "string" && typeof row.subject_model === "string"
        ? <RecordReference model={row.subject_model} id={row.subject_id} /> : null} />
      <Column field="origin" header={t("run.origin")} widget="statusBadge" />
      <Column field="created_at" header={t("run.started")} />
      <Column field="finished_at" header={t("run.finished")} />
      <Column field="outcome" header={t("run.outcome")} />
    </List>
    <Form readOnly returning={["can_cancel", "can_reprocess", "subject_model", "subject_id"]}
      title={({ recordId }) => t("run.title", { id: recordId ?? "" })}
      headerExtras={({ record }) => typeof record?.subject_id === "string" && typeof record.subject_model === "string"
        ? <RecordReference model={record.subject_model} id={record.subject_id} /> : null}>
      <Field name="status" widget="statusbar" />
      <Group label={t("run.facts")} columns={2}>
        <Field name="version.workflow" label={t("run.workflow")} />
        <Field name="version.number" label={t("run.version")} />
        <Field name="origin" label={t("run.origin")} widget="statusBadge" />
        <Field name="run_as" label={t("run.runAs")} />
        <Field name="created_at" label={t("run.started")} />
        <Field name="finished_at" label={t("run.finished")} showWhen={(row) => Boolean(row.finished_at)} />
        <Field name="outcome" label={t("run.outcome")} showWhen={(row) => Boolean(row.outcome)} />
        <Field name="reprocess_of" label={t("run.reprocessOf")} showWhen={(row) => Boolean(row.reprocess_of)} />
        <Field name="parent_step.run" label={t("run.parent")} showWhen={(row) => Boolean(rowValueAtPath(row, "parent_step.run"))} />
        <Field name="trigger_event" label={t("trigger.event")} showWhen={(row) => Boolean(row.trigger_event)} />
      </Group>
      <Field name="failure_reason" label={t("run.failureReason")} widget="textarea"
        showWhen={(row) => row.outcome === "error" && Boolean(row.failure_reason)} />
      <Field name="input" label={t("run.input")} widget="json" />
      <Field name="output" label={t("run.output")} widget="json" />
      <Action id="cancel" label={t("action.cancel_workflow_run")} primary danger
        visibleWhen={(record) => record.can_cancel === true} run={cancel}
        confirm={{ title: t("action.cancel_workflow_run"), body: t("action.cancelDescription"), danger: true }} />
      <Action id="reprocess" label={t("action.reprocess_workflow_run")}
        visibleWhen={(record) => record.can_reprocess === true} run={reprocess}
        confirm={{ title: t("action.reprocess_workflow_run"), body: t("action.reprocessDescription") }} />
    </Form>
  </ResourceList>;
}
