import * as React from "react";
import { rowPublicId } from "@angee/metadata";
import { useAuthoredMutation } from "@angee/refine";
import { Action, Badge, Column, Field, Form, Group, List, ResourceList, TopMenuTabs, useRouteHref, type ActionContext, type RecordTabDescriptor, type StringIdRow } from "@angee/ui";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { PublishWorkflowDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { WorkflowCanvas } from "./WorkflowCanvas";
import { WorkflowRunsPanel, WorkflowVersionsPanel } from "./WorkflowHistoryPanels";
import { WorkflowTriggersPanel } from "./WorkflowTriggersPanel";

const WORKFLOW_MODEL = "workflows.Workflow";
const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";

interface WorkflowHeadRow extends StringIdRow {
  publication_status?: unknown;
  current_published_version?: unknown;
}

export const WORKFLOW_CATALOGUE_FIELDS = ["current_published_version"] as const;

export function WorkflowsPage(): React.ReactElement {
  const t = useWorkflowsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const search = useSearch({ strict: false }) as Readonly<Record<string, unknown>>;
  const collection = search.tab === "sessions" ? "sessions" : "automations";
  const [publishWorkflow] = useAuthoredMutation(PublishWorkflowDocument, {
    invalidateModels: [WORKFLOW_MODEL, STEP_MODEL, EDGE_MODEL],
    errorFrom: (data) => data?.publish_workflow.ok === false ? data.publish_workflow.message : null,
  });
  const publish = React.useCallback(async (context: ActionContext) => {
    const id = rowPublicId(context.record);
    if (!id) return;
    const data = await publishWorkflow({ id });
    context.refresh();
    return data?.publish_workflow?.message;
  }, [publishWorkflow]);
  const openDraft = React.useCallback((context: ActionContext) => {
    const lineageId = context.record?.lineage_id;
    if (typeof lineageId !== "string") return;
    void navigate({ to: routeHref("workflows.workflow", { id: lineageId }) });
  }, [navigate, routeHref]);
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    { id: "editor", label: t("tabs.editor"), icon: "workflow-canvas", render: ({ recordId, reload }) => <WorkflowCanvas workflowId={recordId} onChanged={reload} />, keepMounted: true },
    { id: "runs", label: t("tabs.runs"), icon: "workflow-run", render: ({ recordId }) => <WorkflowRunsPanel workflowId={recordId} /> },
    { id: "versions", label: t("tabs.versions"), icon: "workflow", render: ({ recordId }) => <WorkflowVersionsPanel workflowId={recordId} /> },
    { id: "triggers", label: t("tabs.triggers"), icon: "workflow-trigger", render: ({ recordId }) => <WorkflowTriggersPanel workflowId={recordId} /> },
  ], [t]);

  return (
    <ResourceList<WorkflowHeadRow>
      resource={WORKFLOW_MODEL}
      placement="inline"
      routed
      createDefaults={{ status: "DRAFT" }}
      recordTabs={recordTabs}
      recordPresentation="workspace"
      defaultRecordTab="editor"
      overviewTab={{ label: t("tabs.settings"), position: "last" }}
      baseFilter={{ published_from: { isNull: true }, purpose: { exact: collection === "sessions" ? "AGENT_SESSION" : "AUTOMATION" } }}
      hideCreate={collection === "sessions"}
      toolbarActions={<TopMenuTabs tabs={[
        { id: "automations", label: t("collection.automations"), icon: "workflow" },
        { id: "sessions", label: t("collection.sessions"), icon: "workflow-run" },
      ]} />}
    >
      <List<WorkflowHeadRow> resource={WORKFLOW_MODEL} fields={WORKFLOW_CATALOGUE_FIELDS}>
        <Column field="name" />
        <Column<WorkflowHeadRow> field="publication_status" header={t("col.publicationStatus")} render={(row) => (
          <Badge tone={row.publication_status === "published" ? "success" : "neutral"}>{publicationLabel(row, t)}</Badge>
        )} />
        <Column field="updated_at" />
      </List>
      <Form resource={WORKFLOW_MODEL}>
        <Field name="name" title resolve={(record) => ({ name: "name", title: true, readOnly: record.status !== "DRAFT" })} />
        <Field name="description" resolve={(record) => ({ name: "description", readOnly: record.status !== "DRAFT" })} />
        <Group label={t("form.definition")} columns={2}>
          <Field name="status" readOnly widget="statusbar" />
          <Field name="version" readOnly />
          <Field name="lineage_id" label={t("form.lineage")} readOnly />
          <Field name="error_workflow" resolve={(record) => ({ name: "error_workflow", readOnly: record.status !== "DRAFT" })} />
          <Field name="max_steps" resolve={(record) => ({ name: "max_steps", readOnly: record.status !== "DRAFT" })} />
        </Group>
        <Field name="budget" widget="json" resolve={(record) => ({ name: "budget", widget: "json", readOnly: record.status !== "DRAFT" })} />
        <Action id="publish" label={t("form.publish")} icon="workflow-publish" run={publish} visibleWhen={(record) => record.status === "DRAFT"} />
        <Action id="open-draft" label={t("form.openDraft")} icon="workflow" run={openDraft} visibleWhen={(record) => record.status !== "DRAFT"} />
      </Form>
    </ResourceList>
  );
}

export function publicationLabel(row: WorkflowHeadRow, t: ReturnType<typeof useWorkflowsT>): string {
  if (row.publication_status === "archived") return t("publication.retired");
  if (typeof row.current_published_version === "number") return t("publication.published", { version: row.current_published_version });
  return t("publication.unpublished");
}
