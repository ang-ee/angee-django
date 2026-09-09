import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Column,
  EmptyState,
  ErrorBanner,
  List,
  LoadingPanel,
  useRouteHref,
  type StringIdRow,
} from "@angee/ui";

import { WorkflowLaunchDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";

const WORKFLOW_MODEL = "workflows.Workflow";

type WorkflowCollectionRow = StringIdRow;

export function WorkflowRunsPanel({ workflowId }: { workflowId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const lineage = useWorkflowLineage(workflowId);
  if (lineage.fetching) return <LoadingPanel message={t("lineage.loading")} />;
  if (lineage.error) return <ErrorBanner description={lineage.error.message} />;
  if (!lineage.id) return <EmptyState title={t("lineage.unavailable")} />;
  return (
    <List<WorkflowCollectionRow>
      resource="workflows.WorkflowRun"
      scope="local"
      baseFilter={workflowRunLineageFilter(lineage.id)}
      rowHref={(row) => routeHref("workflows.run", { id: row.id })}
    >
      <Column field="created_at" header={t("runs.started")} />
      <Column field="origin" header={t("runs.origin")} />
      <Column field="status" widget="statusBadge" />
      <Column field="updated_at" />
    </List>
  );
}

export function WorkflowVersionsPanel({ workflowId }: { workflowId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const routeHref = useRouteHref();
  const lineage = useWorkflowLineage(workflowId);
  if (lineage.fetching) return <LoadingPanel message={t("lineage.loading")} />;
  if (lineage.error) return <ErrorBanner description={lineage.error.message} />;
  if (!lineage.id) return <EmptyState title={t("lineage.unavailable")} />;
  return (
    <List<WorkflowCollectionRow>
      resource={WORKFLOW_MODEL}
      scope="local"
      baseFilter={workflowVersionsFilter(lineage.id)}
      rowHref={(row) => routeHref("workflows.workflow", { id: row.id })}
    >
      <Column field="version" />
      <Column field="status" widget="statusBadge" />
      <Column field="created_at" />
      <Column field="updated_at" />
    </List>
  );
}

export function workflowVersionsFilter(lineageId: string) {
  return {
    published_from: { exact: lineageId },
    status: { inList: ["PUBLISHED", "ARCHIVED"] },
  };
}

export function workflowRunLineageFilter(lineageId: string) {
  return {
    OR: [
      { workflow: { exact: lineageId } },
      { "workflow.published_from": { exact: lineageId } },
    ],
  };
}

function useWorkflowLineage(workflowId: string): { id: string | null; fetching: boolean; error: Error | null } {
  const query = useAuthoredQuery(
    WorkflowLaunchDocument,
    { id: workflowId },
    { models: [WORKFLOW_MODEL] },
  );
  return {
    id: query.data?.workflows_by_pk?.lineage_id ?? null,
    fetching: query.isFetching && !query.data,
    error: query.error,
  };
}
