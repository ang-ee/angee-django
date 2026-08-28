import { graphql } from "@angee/gql/console";

/** Typed enum-argument verb; generated id-action documents cover the no-arg verbs. */
export const DropTaskDocument = graphql(`
  mutation ProjectsDropTask($id: ID!, $reason: TaskDroppedReason!) {
    drop_task(id: $id, reason: $reason) {
      ok
      message
      id
      validation_errors
    }
  }
`);
