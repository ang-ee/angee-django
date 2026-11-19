import { graphql } from "@angee/gql/console";

export const ConnectHubSpotDirectory = graphql(`
  mutation ConnectHubSpotDirectory($name: String!, $token: String!) {
    connect_hubspot_directory(name: $name, token: $token) {
      id
      lifecycle
      runtime_status
    }
  }
`);
