// Bespoke console operations owned by the rendered base view layer. Project
// codegen scans framework package `documents.ts` files so shared view primitives
// can consume generated documents without hand-written result shapes.

import { graphql, type DocumentType } from "@angee/gql/console";

export const BaseImplChoices = graphql(`
  query BaseImplChoices($model: String!, $field: String!) {
    impl_choices(model: $model, field: $field) {
      key
      category
      defaults
    }
  }
`);

export type ImplChoice =
  DocumentType<typeof BaseImplChoices>["impl_choices"][number];
