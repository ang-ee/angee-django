import { graphql } from "@angee/gql/console";

export const ConnectHubSpotDirectory = graphql(`
  mutation ConnectHubSpotDirectory($name: String!, $serviceKey: String!) {
    connect_hubspot_directory(name: $name, service_key: $serviceKey) {
      id
      lifecycle
      runtime_status
    }
  }
`);
