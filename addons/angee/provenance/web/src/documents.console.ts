// Authored reads for the provenance console surface. The `revert_changeset`
// ActionResult mutation uses the generated action documents through the shared hooks.

import { graphql, type DocumentType } from "@angee/gql/console";

/** The same compact attribution line in History and Comments. */
export const HistoryEntrySummaryFields = graphql(`
  fragment HistoryEntrySummaryFields on HistoryAttribution {
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
  }
`);

/** One record's changesets, newest first, through the record's own read gate. */
export const RecordHistoryDocument = graphql(`
  query RecordHistory($model: String!, $id: ID!, $limit: Int!, $offset: Int!) {
    history(model: $model, id: $id, limit: $limit, offset: $offset) {
      model
      id
      total
      entries {
        ...HistoryEntrySummaryFields
        can_revert
        changes { model id field old new }
        statements {
          id aspect asserted_by source_kind snak rank status applied confidence history_type values
          source { model id label pointer }
          evidence { model id label pointer }
        }
        merge {
          survivor { model id retained }
          absorbed { model id retained }
        }
      }
    }
  }
`);

/** Applied statements projected onto the fields of one readable record. */
export const FieldProvenanceDocument = graphql(`
  query FieldProvenance($model: String!, $id: ID!) {
    field_provenance(model: $model, id: $id) {
      field
      authority
      origin
      statement_model
      statement_id
    }
  }
`);

export type RecordHistory = NonNullable<DocumentType<typeof RecordHistoryDocument>["history"]>;
export type HistoryEntry = RecordHistory["entries"][number];
export type HistoryStatement = HistoryEntry["statements"][number];
export type FieldProvenance = NonNullable<DocumentType<typeof FieldProvenanceDocument>["field_provenance"]>[number];
