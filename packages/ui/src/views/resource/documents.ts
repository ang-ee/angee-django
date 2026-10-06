// Bespoke console operations owned by the rendered base view layer.

import type { TypedDocumentNode } from "@angee/refine";
import { gql } from "graphql-tag";

/** Mirrors the core-owned `ImplChoice` projection in `angee/graphql/impl.py`. */
export interface ImplChoice {
  key: string;
  category: string;
  defaults: unknown;
  config_schema: unknown | null;
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
      config_schema
    }
  }
`;

/** The in-band outcome the shared trash verbs project (`angee.graphql.actions.ActionResult`). */
export interface TrashOutcomeResult {
  ok: boolean;
  message: string;
  code: string | null;
  validation_errors: unknown;
}

/** Mirrors the graphql addon's shared `trash_record` verb for any trashable resource. */
export const TrashRecord: TypedDocumentNode<
  { trash_record: TrashOutcomeResult },
  { target_type: string; target_id: string; reason: string }
> = gql`
  mutation TrashRecord($target_type: String!, $target_id: ID!, $reason: String!) {
    trash_record(target_type: $target_type, target_id: $target_id, reason: $reason, confirm: true) {
      ok
      message
      code
      validation_errors
    }
  }
`;

/** Mirrors the graphql addon's shared `restore_record` verb for any trashable resource. */
export const RestoreRecord: TypedDocumentNode<
  { restore_record: TrashOutcomeResult },
  { target_type: string; target_id: string }
> = gql`
  mutation RestoreRecord($target_type: String!, $target_id: ID!) {
    restore_record(target_type: $target_type, target_id: $target_id, confirm: true) {
      ok
      message
      code
      validation_errors
    }
  }
`;
