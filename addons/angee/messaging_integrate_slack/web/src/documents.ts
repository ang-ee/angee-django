import { graphql } from "@angee/gql/console";

// Slack connection verifies the user token before creating the channel and its
// static-token credential; polling/sync remains the generic Integration action.
export const ConnectSlackChannel = graphql(`
  mutation ConnectSlackChannel($name: String!, $token: String!) {
    connect_slack_channel(name: $name, token: $token) {
      id
      lifecycle
      runtime_status
    }
  }
`);
