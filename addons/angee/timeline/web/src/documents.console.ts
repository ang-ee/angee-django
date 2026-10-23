import { graphql, type DocumentType } from "@angee/gql/console";

export const TimelineDocument = graphql(`
  query RecordTimelineRail($model: String!, $id: ID!, $kinds: [String!], $needsMe: Boolean = false, $before: String, $limit: Int = 25) {
    timeline(model: $model, id: $id, kinds: $kinds, needs_me: $needsMe, before: $before, limit: $limit) {
      kinds { id count }
      needs_me
      has_more
      next_before
      entries { key at kind title actor detail needs_me target_model target_id children { kind count } }
    }
  }
`);

export type TimelinePage = DocumentType<typeof TimelineDocument>["timeline"];
export type TimelinePageEntry = TimelinePage["entries"][number];
