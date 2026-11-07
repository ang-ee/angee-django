// Bespoke console operations owned by the context aside.

import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";

/** Mirrors the record verb `angee.graphql.edges.RecordEdgeQuery.record_edge_counts`. */
export const RecordEdgeCounts: TypedDocumentNode<
  { record_edge_counts: { edge: string; count: number }[] },
  { model: string; id: string; edges: string[] }
> = gql`
  query RecordEdgeCounts($model: String!, $id: ID!, $edges: [String!]!) {
    record_edge_counts(model: $model, id: $id, edges: $edges) { edge count }
  }
`;
