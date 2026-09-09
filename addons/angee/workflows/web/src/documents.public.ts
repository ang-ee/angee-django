import { graphql, type DocumentType } from "@angee/gql/public";

export const DecideWorkflowDecisionDocument = graphql(`
  mutation DecideWorkflowDecision(
    $decision: ID!
    $verdict: DecisionVerb!
    $payload: JSON
  ) {
    decide(decision: $decision, verdict: $verdict, payload: $payload) {
      decision {
        id
        verdict
        resolution
        updated_at
      }
      validation_errors
    }
  }
`);

export const WorkflowDecisionDocument = graphql(`
  query WorkflowDecision($id: String!) {
    workflow_decisions(where: {id: {_eq: $id}}, limit: 1) {
      id action priority payload verdict resolution attempts max_attempts
      expires_at escalate_at decision_schema workflow_name step_name
      source_run_id source_execution_id source_attempt_id created_at updated_at
    }
  }
`);

export const ScopedWorkflowDecisionDocument = graphql(`
  query ScopedWorkflowDecision($id: String!, $run: String!) {
    workflow_decisions(
      where: {id: {_eq: $id}, step_run__run: {_eq: $run}}
      limit: 1
    ) {
      id
      action
      priority
      payload
      verdict
      resolution
      attempts
      max_attempts
      expires_at
      escalate_at
      decision_schema
      workflow_name
      step_name
      source_run_id
      source_execution_id
      source_attempt_id
      created_at
      updated_at
    }
  }
`);

export type ScopedWorkflowDecision =
  DocumentType<typeof ScopedWorkflowDecisionDocument>["workflow_decisions"][number];

export type PendingWorkflowDecision = ScopedWorkflowDecision;
