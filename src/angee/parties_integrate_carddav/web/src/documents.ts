import { graphql } from "@angee/gql/console";

export const ConnectCardDavDirectory = graphql(`
  mutation ConnectCardDavDirectory(
    $name: String!
    $serverUrl: String!
    $username: String!
    $password: String!
  ) {
    connect_card_dav_directory(name: $name, server_url: $serverUrl, username: $username, password: $password) {
      id
      lifecycle
      runtime_status
    }
  }
`);
