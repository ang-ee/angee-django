import { DecisionCard } from "@angee/decisions";
import { useAuthoredQuery } from "@angee/refine";
import {
  Action, Column, DrawerResourceList, Facet, Field, Form, Group, List, RecordReference,
  optionToken,
} from "@angee/ui";

import { STEP_RUN_MODEL, StepDecisionDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { formatStepPage, stepPageCodec } from "./step-page";
import { WORKFLOW_STEP_STATUS_TONES } from "./status-tones";
import { useStepRetryActions } from "./step-retry";

/** Child collections fetch one selected detail; rows never mount their own queries. */
export function StepRuns({ runId, nodeKeys }: {
  runId: string; nodeKeys?: readonly string[];
}) {
  const t = useWorkflowsT();
  const retryActions = useStepRetryActions();
  return <DrawerResourceList resource={STEP_RUN_MODEL} hideCreate presentation="embedded"
    baseFilter={{ run: { exact: runId }, ...(nodeKeys ? { node_key: { inList: nodeKeys } } : {}) }} recordTabs={[
      { id: "attempts", label: t("step.attempts"), render: ({ recordId }) => <StepAttempts stepId={recordId} /> },
      { id: "records", label: t("step.records"), render: ({ recordId }) =>
        <List resource="workflows.StepRecord" scope="local" presentation="embedded"
          baseFilter={{ step_run: { exact: recordId } }} fields={["record_model", "record_id"]}>
          <Column field="label" header={t("step.records")} render={(row) =>
            typeof row.record_model === "string" && typeof row.record_id === "string"
              ? <RecordReference model={row.record_model} id={row.record_id} label={typeof row.label === "string" ? row.label : undefined} /> : null} />
        </List> },
      { id: "watches", label: t("step.watches"), render: ({ recordId }) =>
        <List resource="workflows.StepWatch" scope="local" presentation="embedded"
          baseFilter={{ step_run: { exact: recordId } }} fields={["record_model"]}>
          <Column field="record_id" header={t("step.watches")} render={(row) =>
            typeof row.record_model === "string" && typeof row.record_id === "string"
              ? <RecordReference model={row.record_model} id={row.record_id} /> : null} />
        </List> },
      { id: "decisions", label: t("step.decisions"), render: ({ recordId }) =>
        <StepDecision stepId={recordId} /> },
    ]}>
    <List fields={["is_mapped", "is_map"]} order={{ rank: "ASC", map_index: "ASC" }} pageSize={10} emptyContent={t("run.noSteps")}>
      <Facet field="status" />
      <Column field="node_label" />
      <Column field="map_index" header={t("step.mapIndex")} showWhen={(row) => row.is_mapped === true} />
      <Column field="map_settled" header={t("step.mapSettled")} showWhen={(row) => row.is_map === true} />
      <Column field="map_total" header={t("step.mapTotal")} showWhen={(row) => row.is_map === true} />
      <Column field="status" header={t("run.status")} widget="statusBadge" tone={WORKFLOW_STEP_STATUS_TONES} />
      <Column field="outcome_label" header={t("run.outcome")} />
      <Column field="attempt" header={t("step.attempts")} />
    </List>
    <Form readOnly returning={["can_retry", "requires_duplicate_acknowledgement"]}>
      <Field name="is_mapped" hidden />
      <Field name="is_map" hidden />
      <Field name="node_label" title />
      <Field name="status" widget="statusbar" status />
      <Group columns={2}>
        <Field name="map_index" label={t("step.mapIndex")} showWhen={(row) => row.is_mapped === true} />
        <Field name="map_settled" label={t("step.mapSettled")} showWhen={(row) => row.is_map === true} />
        <Field name="map_total" label={t("step.mapTotal")} showWhen={(row) => row.is_map === true} />
        <Field name="outcome_label" label={t("run.outcome")} />
        <Field name="attempt" label={t("step.attempts")} />
        <Field name="waiting_kind" label={t("step.waitKind")} showWhen={(row) => optionToken(row.status) === "waiting"} />
        <Field name="awaited_run" label={t("step.awaitedRun")} showWhen={(row) => optionToken(row.waiting_kind) === "run"} />
        <Field name="wait_reason" label={t("step.waitReason")} showWhen={(row) => optionToken(row.status) === "waiting"} />
        <Field name="page_index" label={t("step.page")} widget="text" valueCodec={stepPageCodec(t)} />
        <Field name="retries" label={t("step.retries")} />
        <Field name="deadline_at" label={t("step.deadline")} showWhen={(row) => Boolean(row.deadline_at)} />
        <Field name="wake_at" label={t("step.wake")} showWhen={(row) => Boolean(row.wake_at)} />
        <Field name="created_at" label={t("step.created")} />
        <Field name="updated_at" label={t("step.updated")} />
      </Group>
      <Field name="failure_reason" label={t("run.failureReason")} widget="textarea"
        showWhen={(row) => Boolean(row.failure_reason)} />
      <Field name="input" label={t("run.input")} widget="json" />
      <Field name="output" label={t("run.output")} widget="json" />
      <Field name="state" label={t("step.checkpoint")} widget="json" />
      {retryActions.map((action) => <Action key={action.id} {...action} />)}
    </Form>
  </DrawerResourceList>;
}

function StepDecision({ stepId }: { stepId: string }) {
  const query = useAuthoredQuery(StepDecisionDocument, { id: stepId }, { models: [STEP_RUN_MODEL, "decisions.Decision"] });
  const decision = query.data?.steprun_by_pk?.decision;
  return decision ? <DecisionCard decision={decision} /> : null;
}

function StepAttempts({ stepId }: { stepId: string }) {
  const t = useWorkflowsT();
  return <DrawerResourceList resource="workflows.StepAttempt" hideCreate presentation="embedded"
    baseFilter={{ step_run: { exact: stepId } }}>
    <List order={{ number: "ASC" }}>
      <Column field="number" />
      <Column field="page_index" header={t("step.page")} render={(row) => formatStepPage(row.page_index, t)} />
      <Column field="result" header={t("step.result")} widget="statusBadge" />
      <Column field="started_at" header={t("run.started")} />
      <Column field="finished_at" header={t("run.finished")} />
      <Column field="error" header={t("run.retainedError")} />
    </List>
    <Form readOnly>
      <Field name="number" title />
      <Field name="page_index" label={t("step.page")} widget="text" valueCodec={stepPageCodec(t)} />
      <Field name="result" label={t("step.result")} widget="statusBadge" />
      <Field name="started_at" label={t("run.started")} />
      <Field name="finished_at" label={t("run.finished")} />
      <Field name="error" label={t("run.retainedError")} widget="textarea" />
      <Field name="stacktrace" label={t("step.stacktrace")} widget="textarea" />
    </Form>
  </DrawerResourceList>;
}
