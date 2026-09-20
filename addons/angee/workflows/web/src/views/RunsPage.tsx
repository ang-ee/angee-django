import * as React from "react";
import type { ActionFieldName } from "@angee/gql/console/actions";
import { rowPublicId } from "@angee/metadata";
import { useActionMutation, useAuthoredMutation } from "@angee/refine";
import {
  Action,
  Column,
  Facet,
  Field,
  Form,
  Group,
  List,
  ResourceList,
  routeSearchParam,
  TopMenuTabs,
  useResourceRecordHrefLookup,
  useRouteSearch,
  type ActionContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { CancelWorkflowRunDocument } from "../documents.console";
import { DECISION_SEARCH_KEY, decisionSearch } from "../decision-navigation";
import { useWorkflowsT } from "../i18n";
import {
  RunTimelinePanel,
  TERMINAL_RUN_STATUSES,
  runCollectionFilter,
  runOriginLabel,
  waitLabel,
} from "./RunInspection";
import { WorkflowApprovals } from "./WorkflowApprovals";

const RUN_MODEL = "workflows.WorkflowRun";
const STEP_RUN_MODEL = "workflows.StepRun";
const DECISION_MODEL = "workflows.Decision";

interface WorkflowRunRow extends StringIdRow {
  origin?: unknown;
  occurrence_id?: unknown;
  status?: unknown;
  waiting_kind?: unknown;
  next_wake_at?: unknown;
}
export function RunsPage(): React.ReactElement {
  const t = useWorkflowsT();
  const navigate = useNavigate();
  const recordHref = useResourceRecordHrefLookup();
  const search = useRouteSearch();
  const decisionId = routeSearchParam(search, DECISION_SEARCH_KEY) ?? null;
  const collection = search.tab === "sessions" ? "sessions" : "automations";
  const waitOptions = React.useMemo(
    () => [
      { value: "scheduled", label: t("runs.waitScheduled") },
      { value: "approval", label: t("runs.waitApproval") },
      { value: "external", label: t("runs.waitExternal") },
      { value: "children", label: t("runs.waitChildren") },
    ],
    [t],
  );
  // Correct as-is: WorkflowRun and Decision have changes(), while StepRun is read by an authored query.
  const [cancelRun] = useAuthoredMutation(CancelWorkflowRunDocument, {
    invalidateModels: [RUN_MODEL, STEP_RUN_MODEL, DECISION_MODEL],
    errorFrom: (data) =>
      data?.cancel_workflow_run.ok === false ? data.cancel_workflow_run.message : null,
  });
  const [reprocessRun] = useActionMutation<ActionFieldName>("reprocess_workflow_run", {
    idArgument: "run",
    invalidateModels: [RUN_MODEL, STEP_RUN_MODEL, DECISION_MODEL],
  });
  const reprocessKeys = React.useRef(new Map<string, string>());
  const cancel = React.useCallback(
    async (context: ActionContext) => {
      const id = rowPublicId(context.record);
      if (!id) return;
      const data = await cancelRun({ id });
      context.refresh();
      return data?.cancel_workflow_run?.message;
    },
    [cancelRun],
  );
  const reprocessById = React.useCallback(async (id: string) => {
    let requestKey = reprocessKeys.current.get(id);
    if (!requestKey) {
      requestKey = crypto.randomUUID();
      reprocessKeys.current.set(id, requestKey);
    }
    const outcome = await reprocessRun(id, { request_key: requestKey });
    if (outcome?.ok) reprocessKeys.current.delete(id);
    if (outcome?.ok && outcome.id) {
      const href = recordHref(RUN_MODEL, outcome.id);
      if (href) void navigate({ to: href });
    }
    return outcome?.message;
  }, [navigate, recordHref, reprocessRun]);
  const reprocess = React.useCallback(async (context: ActionContext) => {
    const id = rowPublicId(context.record);
    if (!id) return;
    const message = await reprocessById(id);
    context.refresh();
    return message;
  }, [reprocessById]);
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "timeline",
        label: t("tabs.timeline"),
        icon: "workflow-run",
        render: ({ recordId }) => <RunTimelinePanel runId={recordId} onReprocess={() => reprocessById(recordId)} />,
        keepMounted: true,
      },
      {
        id: "approvals",
        label: t("inbox.title"),
        icon: "workflow-inbox",
        render: ({ recordId }) => (
          <WorkflowApprovals
            runId={recordId}
            decisionId={decisionId}
            selectedTaskOnly={Boolean(decisionId)}
            onDecisionChange={(decision) => {
              void navigate({
                to: ".",
                replace: true,
                search: (previous: Record<string, unknown>) => decisionSearch(previous, decision),
              });
            }}
          />
        ),
        keepMounted: true,
      },
    ],
    [decisionId, navigate, reprocessById, t],
  );

  return (
    <ResourceList
      resource={RUN_MODEL}
      placement="inline"
      routed
      hideCreate
      recordTabs={recordTabs}
      recordPresentation="workspace"
      defaultRecordTab="timeline"
      overviewTab={{ label: t("tabs.details"), position: "last" }}
      baseFilter={runCollectionFilter(collection)}
      toolbarActions={
        <TopMenuTabs
          tabs={[
            {
              id: "automations",
              label: t("runs.collectionAutomations"),
              icon: "workflow",
            },
            {
              id: "sessions",
              label: t("runs.collectionSessions"),
              icon: "workflow-run",
            },
          ]}
        />
      }
    >
      <List<WorkflowRunRow> resource={RUN_MODEL} defaultGroup={{ field: "status" }}>
        <Facet field="workflow" label={t("col.workflow")} />
        <Column field="workflow.name" header={t("col.workflow")} />
        <Column<WorkflowRunRow> field="origin" header={t("runs.origin")} render={(row) => runOriginLabel(row.origin, t)} />
        <Column field="status" widget="statusBadge" />
        <Column field="reprocessed_from" />
        <Column<WorkflowRunRow>
          field="waiting_kind"
          header={t("runs.waitingFor")}
          render={(row) => waitLabel(row.waiting_kind, row.status, t)}
        />
        <Column field="next_wake_at" header={t("runs.nextWake")} />
        <Column field="steps_taken" />
        <Column field="updated_at" />
      </List>
      <Form resource={RUN_MODEL}>
        <Field name="workflow" readOnly title />
        <Field name="origin" label={t("runs.origin")} readOnly />
        <Field name="occurrence_id" label={t("runs.occurrence")} readOnly />
        <Group label={t("runs.timeline")} columns={2}>
          <Field name="status" readOnly widget="statusbar" />
          <Field name="waiting_kind" readOnly options={waitOptions} />
          <Field name="next_wake_at" readOnly />
          <Field name="steps_taken" readOnly />
          <Field name="reprocessed_from" readOnly />
          <Field name="updated_at" readOnly />
        </Group>
        <Field name="budget_spent" widget="json" readOnly />
        <Field name="error" readOnly />
        <Action
          id="reprocess"
          label={t("runs.reprocess")}
          icon="refresh"
          run={reprocess}
          visibleWhen={(record) => TERMINAL_RUN_STATUSES.has(String(record.status))}
        />
        <Action
          id="cancel"
          label={t("form.cancel")}
          icon="workflow-cancel"
          danger
          run={cancel}
          visibleWhen={(record) => !TERMINAL_RUN_STATUSES.has(String(record.status))}
        />
      </Form>
    </ResourceList>
  );
}
