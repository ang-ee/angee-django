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
      created_at
      updated_at
      step_run {
        id
        status
        outcome
        step {
          id
          key
          name
          workflow {
            id
            name
          }
        }
        run {
          id
          status
          workflow {
            id
            name
          }
        }
      }
    }
  }
`);

export const DecideWorkflowDecisionDocument = graphql(`
  mutation DecideWorkflowDecision(
    $decision: ID!
    $verdict: String!
    $payload: JSON
  ) {
    decide(decision: $decision, verdict: $verdict, payload: $payload) {
      id
      verdict
      resolution
      updated_at
    }
  }
`);

export type PendingWorkflowDecision =
  DocumentType<typeof PendingWorkflowDecisionsDocument>["workflow_decisions"][number];
