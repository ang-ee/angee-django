import { graphql } from "@angee/gql/console";

export const CaptureNeedDocument = graphql(`
  mutation IntakeCaptureNeed(
    $target: NeedTargetInput!
    $body: String!
    $party: ID
    $importance: NeedImportance!
  ) {
    capture_need(
      target: $target
      body: $body
      party: $party
      importance: $importance
    ) {
      ok
      message
      id
      code
      validation_errors
    }
  }
`);

/** The Need owner supplies the current access seat and revision for Task verbs. */
export const TaskAccessNeedsDocument = graphql(`
  query IntakeTaskAccessNeeds($task: String!) {
    intake_needs(where: { task: { _eq: $task } }) {
      id
      revision
      permissions
      claimed_name
      access_decision { id is_open verdict }
    }
  }
`);

export const DecideNeedAccessDocument = graphql(`
  mutation IntakeDecideNeedAccess($need: ID!, $action: NeedAccessAction!, $expected_revision: Int!) {
    decide_need_access(need: $need, action: $action, expected_revision: $expected_revision) {
      ok message id code validation_errors
    }
  }
`);
