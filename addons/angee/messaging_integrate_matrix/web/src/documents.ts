import { graphql } from "@angee/gql/console";

export const ConnectMatrixChannel = graphql(`
  mutation ConnectMatrixChannel($homeserver: String!, $username: String!, $password: String!) {
    connect_matrix_channel(
      homeserver: $homeserver
      username: $username
      password: $password
    ) {
      id
      lifecycle
      runtime_status
    }
  }
`);
