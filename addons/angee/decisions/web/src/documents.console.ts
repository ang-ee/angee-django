import { graphql, type DocumentType } from "@angee/gql/console";

export const DECISION_MODEL = "decisions.Decision";
export const DECISION_MODELS = [DECISION_MODEL, "decisions.DecisionRecord"];

export const DecisionFields = graphql(`
  fragment DecisionCardFields on DecisionType {
    id kind kind_label revision created_at is_open permissions context proposal verdict verdict_values verdict_label answered_at
    records { id record_model record_id }
    requester { display_name }
    assignees { display_name }
    answered_by { display_name }
  }
`);

export const DecisionDocument = graphql(`
  query DecisionDetail($id: String!) {
    decisions_by_pk(id: $id) { ...DecisionCardFields }
  }
`);

export type Decision = DocumentType<typeof DecisionFields>;

export const DecideDocument = graphql(`
  mutation Decide($id: ID!, $revision: Int!, $chosen: [String!]!, $values: JSON) {
    decide(id: $id, revision: $revision, chosen: $chosen, values: $values) { ok message id code validation_errors }
  }
`);
