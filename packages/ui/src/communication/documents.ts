// Bespoke console operations owned by the context aside.

import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";

/** Mirrors the record verb `angee.graphql.related_counts.RelatedCountQuery.related_counts`. */
export const RelatedCounts: TypedDocumentNode<
  { related_counts: { group: string; count: number }[] },
  { model: string; id: string; groups: string[] }
> = gql`
  query RelatedCounts($model: String!, $id: ID!, $groups: [String!]!) {
    related_counts(model: $model, id: $id, groups: $groups) { group count }
  }
`;
