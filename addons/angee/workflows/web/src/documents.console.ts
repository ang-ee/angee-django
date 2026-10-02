import { graphql } from "@angee/gql/console";

export const RUN_MODEL = "workflows.WorkflowRun";
export const STEP_RUN_MODEL = "workflows.StepRun";
export const STEP_EVIDENCE_MODELS = ["workflows.StepAttempt", "workflows.StepArtifact"] as const;
export const RUN_MODELS = [RUN_MODEL, STEP_RUN_MODEL, "workflows.StepWatch", ...STEP_EVIDENCE_MODELS] as const;

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
      id permissions draft draft_revision layout draft_outcomes published { number }
    }
    workflow_step_choices(id: $target) {
      key label icon category defaults config_schema internal outcomes
    }
  }
`);

export const WorkflowStepPortsDocument = graphql(`
  query WorkflowStepPorts($id: ID!, $configurations: [WorkflowStepConfiguration!]!) {
    workflow_step_ports(id: $id, configurations: $configurations) { node outcomes }
  }
`);

export const SaveWorkflowDraftDocument = graphql(`
  mutation SaveWorkflowDraft($id: ID!, $draft: JSON!, $layout: JSON!, $revision: Int!) {
    save_workflow_draft(id: $id, draft: $draft, layout: $layout, expected_revision: $revision) {
      status issues message data { revision diagnostics }
    }
  }
`);

export const PublishWorkflowDocument = graphql(`
  mutation PublishWorkflow($id: ID!) {
    publish_workflow(id: $id) { status issues message data { number dependents } }
  }
`);
