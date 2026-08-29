import { graphql } from "@angee/gql/console";

// The polymorphic tag edge is not an ordinary resource insert, so it is read and
// written through authored operations (the backend's `TagQuery`/`TagMutation`).
// The console SDL types the args as `ID!` and names them snake_case, matching the
// Hasura-shaped surface the rest of the console uses. `RecordTagsPane` fires these
// through the `@angee/refine` authored hooks against the record addressed by the
// active chatter view (`view.type` is the REBAC resource type = `target_type`,
// `view.sqid` = `target_id`).

/** The tags applied to one record (REBAC-scoped by tag reach). */
export const TagAssignmentsDocument = graphql(`
  query TagAssignments($targetType: String!, $targetId: ID!) {
    tag_assignments(target_type: $targetType, target_id: $targetId) {
      id
      created_at
      tag {
        id
        name
        color
      }
    }
  }
`);

/** The full tag vocabulary readable by the actor — the pane's add palette. */
export const TagOptionsDocument = graphql(`
  query TagOptions {
    tags(order_by: [{ name: asc }]) {
      id
      name
      color
    }
  }
`);

/** Attach tags to a record (idempotent per edge). */
export const TagDocument = graphql(`
  mutation Tag($targetType: String!, $targetId: ID!, $tagIds: [ID!]!) {
    tag(target_type: $targetType, target_id: $targetId, tag_ids: $tagIds) {
      id
      tag {
        id
        name
        color
      }
    }
  }
`);

/** Detach tags from a record. */
export const UntagDocument = graphql(`
  mutation Untag($targetType: String!, $targetId: ID!, $tagIds: [ID!]!) {
    untag(target_type: $targetType, target_id: $targetId, tag_ids: $tagIds)
  }
`);
