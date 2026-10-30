import { graphql } from "@angee/gql/console";

export const TagRecord = graphql(`
  mutation TagRecord($type: String!, $id: ID!, $tags: [ID!]!) {
    tag(target_type: $type, target_id: $id, tag_ids: $tags) { id }
  }
`);
export const UntagRecord = graphql(`
  mutation UntagRecord($type: String!, $id: ID!, $tags: [ID!]!) {
    untag(target_type: $type, target_id: $id, tag_ids: $tags)
  }
`);
