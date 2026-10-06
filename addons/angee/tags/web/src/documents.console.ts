import { graphql } from "@angee/gql/console";

// The polymorphic tag edge is not an ordinary resource insert, so it is written
// through authored operations (the backend's `TagMutation`). The console SDL
// types the args as `ID!` and names them snake_case, matching the Hasura-shaped
// surface the rest of the console uses. `TagsField` fires these through the
// `@angee/ui` resource-aware authored mutation against the open record
// (`target_type` is the model's REBAC resource type from its metadata,
// `target_id` its public id) and reads the tags back through the record's own
// `tags` relation list.

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
