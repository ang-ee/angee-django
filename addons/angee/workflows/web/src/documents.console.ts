import { graphql } from "@angee/gql/console";

export const RUN_MODEL = "workflows.WorkflowRun";
export const STEP_RUN_MODEL = "workflows.StepRun";
export const STEP_EVIDENCE_MODELS = ["workflows.StepAttempt", "workflows.StepArtifact"] as const;
export const RUN_MODELS = [RUN_MODEL, STEP_RUN_MODEL, "workflows.StepWatch", ...STEP_EVIDENCE_MODELS] as const;

export const TriggerGrantsDocument = graphql(`
  query TriggerGrants($id: String!) {
    trigger_by_pk(id: $id) {
      id can_edit enabled disabled_reason
      grants { resource_type resource_id relation target_label }
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
      id node_key map_index is_mapped
      run { id version { workflow { key name } } }
    }
  }
`);
