import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Column,
  EmptyState,
  ErrorBanner,
  Field,
  Form,
  Group,
  List,
  LoadingPanel,
  ResourceList,
  REFINE_CREATE_ID,
  useEnumOptions,
} from "@angee/ui";

import { WorkflowLaunchDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";

const WORKFLOW_MODEL = "workflows.Workflow";
const TRIGGER_MODEL = "workflows.Trigger";

export function WorkflowTriggersPanel({ workflowId }: { workflowId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const query = useAuthoredQuery(WorkflowLaunchDocument, { id: workflowId }, { models: [WORKFLOW_MODEL] });
  if (query.isFetching && !query.data) return <LoadingPanel message={t("triggers.loading")} />;
  if (query.error && !query.data) return <ErrorBanner description={errorMessage(query.error)} />;
  const workflow = query.data?.workflows_by_pk;
  if (!workflow) return <EmptyState title={t("triggers.unavailable")} />;
  return <WorkflowTriggerCollection key={workflow.lineage_id} workflowId={workflow.lineage_id} readOnly={workflow.status !== "DRAFT"} />;
}

function WorkflowTriggerCollection({ workflowId, readOnly }: { workflowId: string; readOnly: boolean }): React.ReactElement {
  const t = useWorkflowsT();
  const triggerKindOptions = useEnumOptions(TRIGGER_MODEL, "kind");
  const [recordId, setRecordId] = React.useState<string | undefined>();
  return (
    <ResourceList resource={TRIGGER_MODEL} scope="local" placement="inline" baseFilter={{ workflow: { exact: workflowId } }} createDefaults={{ workflow: workflowId }} recordId={recordId} onSelect={(id) => setRecordId(id ?? REFINE_CREATE_ID)} onClose={() => setRecordId(undefined)} hideCreate={readOnly}>
      <List resource={TRIGGER_MODEL}>
        <Column field="kind" />
        <Column field="enabled" />
        <Column field="next_fire_at" />
        <Column field="updated_at" />
      </List>
      <Form resource={TRIGGER_MODEL}>
        <Group label={t("triggers.details")} columns={2}>
          <Field name="workflow" createOnly />
          <Field name="kind" widget="select" options={triggerKindOptions} createOnly readOnly={readOnly} />
          <Field name="enabled" readOnly={readOnly} />
          <Field name="next_fire_at" readOnly />
        </Group>
        <Field name="config" widget="json" readOnly={readOnly} />
      </Form>
    </ResourceList>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
