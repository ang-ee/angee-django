import { useMemo } from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { DecisionsList } from "@angee/decisions";
import {
  Action, Column, DrawerResourceList, Facet, Field, Form, Group, List, RecordReference,
  optionToken, useActionOutcomeMutation, useAppRuntime, useRecordActionMutation, useUiT,
} from "@angee/ui";
import { jsonSchemaActionArgs } from "@angee/ui/views/json-schema";

import { RUN_MODELS, STEP_RUN_MODEL } from "./documents.console";
import { useWorkflowsT } from "./i18n";

/** Child collections fetch one selected detail; rows never mount their own queries. */
export function StepRuns({ runId, nodeKeys, selectFirstRecord = false }: {
  runId: string; nodeKeys?: readonly string[]; selectFirstRecord?: boolean;
}) {
  const t = useWorkflowsT();
  const { widgets } = useAppRuntime();
  const uiT = useUiT();
  const [retry] = useRecordActionMutation<ActionFieldName>("retry_step", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const [retryDuplicate] = useActionOutcomeMutation<ActionFieldName>("retry_step_accepting_duplicate", {
    dataProviderName: "console", invalidateModels: RUN_MODELS,
  });
  const acknowledgement = useMemo(() => jsonSchemaActionArgs({
    type: "object", properties: { acknowledge: { type: "boolean", const: true, default: false,
      label: t("action.acknowledge"), description: t("action.duplicateDescription") } },
    required: ["acknowledge"],
  }, widgets, { translate: uiT }), [t, uiT, widgets]);
  return <DrawerResourceList resource={STEP_RUN_MODEL} hideCreate presentation="embedded"
    selectFirstRecord={selectFirstRecord}
    baseFilter={{ run: { exact: runId }, ...(nodeKeys ? { node_key: { inList: nodeKeys } } : {}) }} recordTabs={[
      { id: "attempts", label: t("step.attempts"), render: ({ recordId }) => <StepAttempts stepId={recordId} /> },
      { id: "artifacts", label: t("step.artifacts"), render: ({ recordId }) =>
        <List resource="workflows.StepArtifact" scope="local" presentation="embedded"
          baseFilter={{ step_run: { exact: recordId } }} fields={["record_model", "record_id"]}>
          <Column field="label" header={t("step.artifacts")} render={(row) =>
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
        <DecisionsList baseFilter={{ "group.step_run": { exact: recordId } }} /> },
    ]}>
    <List fields={["is_mapped", "is_map"]} order={{ rank: "ASC", map_index: "ASC" }} pageSize={10} emptyContent={t("run.noSteps")}>
      <Facet field="status" />
      <Column field="node_label" />
      <Column field="map_index" header={t("step.mapIndex")} showWhen={(row) => row.is_mapped === true} />
      <Column field="map_settled" header={t("step.mapSettled")} showWhen={(row) => row.is_map === true} />
      <Column field="map_total" header={t("step.mapTotal")} showWhen={(row) => row.is_map === true} />
      <Column field="status" header={t("run.status")} widget="statusBadge" />
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
        <Field name="page_index" label={t("step.pageIndex")} />
        <Field name="retries" label={t("step.retries")} />
        <Field name="deadline_at" label={t("step.deadline")} showWhen={(row) => Boolean(row.deadline_at)} />
        <Field name="wake_at" label={t("step.wake")} showWhen={(row) => Boolean(row.wake_at)} />
        <Field name="created_at" label={t("run.started")} />
        <Field name="updated_at" label={t("step.updated")} />
      </Group>
      <Field name="failure_reason" label={t("run.failureReason")} widget="textarea"
        showWhen={(row) => Boolean(row.failure_reason)} />
      <Field name="input" label={t("run.input")} widget="json" />
      <Field name="output" label={t("run.output")} widget="json" />
      <Field name="state" label={t("step.checkpoint")} widget="json" />
      <Action id="retry" label={t("action.retry_step")} placement="toolbar" primary run={retry}
        visibleWhen={(row) => row.can_retry === true && row.requires_duplicate_acknowledgement !== true}
        confirm={{ title: t("action.retry_step"), body: t("action.retryDescription") }} />
      <Action id="retry-duplicate" label={t("action.retry_step_accepting_duplicate")} placement="toolbar" primary danger
        visibleWhen={(row) => row.can_retry === true && row.requires_duplicate_acknowledgement === true}
        args={acknowledgement}
        submit={(_values, { record }) => typeof record?.id === "string" ? retryDuplicate(record.id) : null} />
    </Form>
  </DrawerResourceList>;
}

function StepAttempts({ stepId }: { stepId: string }) {
  const t = useWorkflowsT();
  return <DrawerResourceList resource="workflows.StepAttempt" hideCreate presentation="embedded"
    baseFilter={{ step_run: { exact: stepId } }}>
    <List order={{ number: "ASC" }}>
      <Column field="number" />
      <Column field="page_index" header={t("step.pageIndex")} />
      <Column field="result" header={t("step.result")} widget="statusBadge" />
      <Column field="started_at" header={t("run.started")} />
      <Column field="finished_at" header={t("run.finished")} />
      <Column field="error" header={t("run.retainedError")} />
    </List>
    <Form readOnly>
      <Field name="number" title />
      <Field name="page_index" label={t("step.pageIndex")} />
      <Field name="result" label={t("step.result")} widget="statusBadge" />
      <Field name="started_at" label={t("run.started")} />
      <Field name="finished_at" label={t("run.finished")} />
      <Field name="error" label={t("run.retainedError")} widget="textarea" />
      <Field name="stacktrace" label={t("step.stacktrace")} widget="textarea" />
    </Form>
  </DrawerResourceList>;
}
