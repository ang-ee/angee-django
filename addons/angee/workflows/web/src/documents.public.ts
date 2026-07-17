import { graphql, type DocumentType } from "@angee/gql/public";

export const PendingWorkflowDecisionsDocument = graphql(`
  query PendingWorkflowDecisions($limit: Int!, $offset: Int!) {
    workflow_decisions(
      where: { verdict: { _eq: "pending" } }
      order_by: [{ priority: asc }, { created_at: asc }]
      limit: $limit
      offset: $offset
    ) {
      id
      action
      priority
      payload
      verdict
      attempts
      max_attempts
      expires_at
      escalate_at
      decision_schema
      workflow_name
      step_name
      created_at
      updated_at
    }
  }
`);

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

export type PendingWorkflowDecision =
  DocumentType<typeof PendingWorkflowDecisionsDocument>["workflow_decisions"][number];
