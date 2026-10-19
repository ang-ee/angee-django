import { graphql, type DocumentType } from "@angee/gql/console";

export const TimelineDocument = graphql(`
  query RecordTimelineRail($model: String!, $id: ID!, $kinds: [String!], $before: String, $limit: Int = 25) {
    timeline(model: $model, id: $id, kinds: $kinds, before: $before, limit: $limit) {
      kinds
      has_more
      next_before
      counts { kind count }
      entries { id at kind title actor detail needs_me target_model target_id group_id }
    }
  }
`);

export type TimelinePage = DocumentType<typeof TimelineDocument>["timeline"];
export type TimelinePageEntry = TimelinePage["entries"][number];
