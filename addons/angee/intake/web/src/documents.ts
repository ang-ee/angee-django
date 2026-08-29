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
      validation_errors
    }
  }
`);
