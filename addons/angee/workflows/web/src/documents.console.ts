import { graphql } from "@angee/gql/console";

export const RUN_MODEL = "workflows.WorkflowRun";
export const STEP_RUN_MODEL = "workflows.StepRun";
export const STEP_EVIDENCE_MODELS = ["workflows.StepAttempt", "workflows.StepArtifact"] as const;
export const RUN_MODELS = [RUN_MODEL, STEP_RUN_MODEL, "workflows.StepWatch", ...STEP_EVIDENCE_MODELS] as const;

export const WorkflowRunGraphDocument = graphql(`
  query WorkflowRunGraph($id: String!) {
    workflowrun_by_pk(id: $id) {
      id
      graph {
        nodes {
          key label step_label rank body_key outcomes { outcome label }
          item_counts { status count } item_attempts
          step_run {
            id status waiting_kind wait_reason outcome outcome_label failure_reason
            attempt page_index map_total map_settled created_at updated_at deadline_at wake_at
          }
        }
        edges { source outcome target taken }
      }
    }
  }
`);

export const TriggerGrantsDocument = graphql(`
  query TriggerGrants($id: String!) {
    trigger_by_pk(id: $id) {
      id can_edit enabled disabled_reason
      grants { resource_type resource_id relation target_label target_kind }
    }
  }
`);

export const RevokeWorkflowTriggerGrantDocument = graphql(`
  mutation RevokeWorkflowTriggerGrant($id: ID!, $resourceType: String!, $resourceId: String!, $relation: String!) {
    revoke_workflow_trigger_grant(
      id: $id, resource_type: $resourceType, resource_id: $resourceId, relation: $relation
    ) { ok message id }
  }
`);

export const DecisionWaitingRunsDocument = graphql(`
  query DecisionWaitingRuns($group: String!) {
    steprun(where: { decision_group: { _eq: $group }, status: { _eq: "waiting" } }, order_by: [{ rank: asc }, { map_index: asc }]) {
      id node_label map_index is_mapped
      run { id display_name }
    }
  }
`);


export const WorkflowStudioDocument = graphql(`
  query WorkflowStudio($id: String!, $target: ID!) {
    workflow_by_pk(id: $id) {
      id permissions draft draft_revision layout published { number }
    }
    workflow_step_choices(id: $target) {
      key label icon category defaults config_schema internal outcomes
    }
  }
`);

export const WorkflowStepOutcomesDocument = graphql(`
  query WorkflowStepOutcomes($id: ID!, $configurations: [WorkflowStepConfiguration!]!) {
    workflow_step_outcomes(id: $id, configurations: $configurations) { node outcomes issues { node path code message } }
  }
`);

export const SaveWorkflowDraftDocument = graphql(`
  mutation SaveWorkflowDraft($id: ID!, $draft: JSON!, $layout: JSON!, $revision: Int!, $nodeKeys: JSON!) {
    save_workflow_draft(id: $id, draft: $draft, layout: $layout, expected_revision: $revision, node_keys: $nodeKeys) {
      revision diagnostics { node path code message }
    }
  }
`);

export const PublishWorkflowDocument = graphql(`
  mutation PublishWorkflow($id: ID!, $revision: Int!) {
    publish_workflow(id: $id, expected_revision: $revision) { number dependents }
  }
`);
