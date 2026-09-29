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

export const TaskAccessDecisionsDocument = graphql(`
  query IntakeTaskAccessDecisions($task: String!) {
    intake_needs(where: {task: {_eq: $task}}) { id access_decision { id } }
  }
`);
