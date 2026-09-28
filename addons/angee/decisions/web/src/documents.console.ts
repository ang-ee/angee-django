import { graphql, type DocumentType } from "@angee/gql/console";

export const DECISION_MODEL = "decisions.Decision";
export const DECISION_MODELS = [DECISION_MODEL, "decisions.DecisionGroup"];

export const DecisionDocument = graphql(`
  query DecisionDetail($id: String!) {
    decisions_by_pk(id: $id) {
      id kind revision is_open can_act form_schema basis context
      verdict closed_reason resolution resolved_at expires_at
      record_model_label record_public_id
      requester { display_name }
      resolved_by { display_name }
      group { id }
    }
  }
`);

export const DecisionSeatsDocument = graphql(`
  query DecisionSeats($group: String!) {
    decisions(where: {group: {_eq: $group}}, order_by: [{index: asc}]) {
      id index verdict closed_reason
      assignees { display_name }
    }
  }
`);

export type Decision = NonNullable<DocumentType<typeof DecisionDocument>["decisions_by_pk"]>;
export type DecisionSeat = DocumentType<typeof DecisionSeatsDocument>["decisions"][number];
