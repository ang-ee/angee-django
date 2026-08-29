// Bespoke console operations owned by the rendered base view layer.

import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";

/** Mirrors the core-owned `ImplChoice` projection in `angee/graphql/impl.py`. */
export interface ImplChoice {
  key: string;
  category: string;
  defaults: unknown;
}

interface BaseImplChoicesResult {
  impl_choices: ImplChoice[];
}

type BaseImplChoicesVariables = {
  model: string;
  field: string;
};

export const BaseImplChoices: TypedDocumentNode<
  BaseImplChoicesResult,
  BaseImplChoicesVariables
> = gql`
  query BaseImplChoices($model: String!, $field: String!) {
    impl_choices(model: $model, field: $field) {
      key
      category
      defaults
    }
  }
`;
