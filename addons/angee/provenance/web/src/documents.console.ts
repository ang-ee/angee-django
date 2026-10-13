// Authored reads for the provenance console surface. The `revert_changeset`
// ActionResult mutation uses the generated action documents through the shared hooks.

import { graphql, type DocumentType } from "@angee/gql/console";

/** One record's changesets, newest first, through the record's own read gate. */
export const RecordHistoryDocument = graphql(`
  query RecordHistory($model: String!, $id: ID!, $limit: Int!, $offset: Int!) {
    history(model: $model, id: $id, limit: $limit, offset: $offset) {
      model
      id
      total
      entries {
        id
        actor
        actor_model
        actor_id
        source_kind
        action
        activity
        change_reason
        occurred_at
        recorded_at
        outcome
        can_revert
        changes { field old new }
        statements { id aspect asserted_by source_kind snak rank status applied confidence history_type values }
        merge {
          survivor { model id retained }
          absorbed { model id retained }
        }
      }
    }
  }
`);

export type RecordHistory = NonNullable<DocumentType<typeof RecordHistoryDocument>["history"]>;
export type HistoryEntry = RecordHistory["entries"][number];
export type HistoryStatement = HistoryEntry["statements"][number];
export type HistoryFieldChange = HistoryEntry["changes"][number];
