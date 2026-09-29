import { graphql } from "@angee/gql/console";

export const DecisionRecordDocument = graphql(`
  query HumanDecisionRecord($id: String!) {
    decisions_by_pk(id: $id) {
      id revision kind verdict closed_reason is_open can_revisit permissions form_schema
    }
  }
`);

export const SubjectDecisionsDocument = graphql(`
  query SubjectDecisions($model: String!, $id: ID!) {
    subject_decisions(model_label: $model, record_id: $id) { id }
  }
`);
