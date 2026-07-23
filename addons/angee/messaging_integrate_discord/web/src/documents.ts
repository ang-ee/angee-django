import { graphql } from "@angee/gql/console";

export const ConnectDiscordChannel = graphql(`
  mutation ConnectDiscordChannel($name: String!, $token: String!) {
    connect_discord_channel(name: $name, token: $token) {
      id
      lifecycle
      runtime_status
    }
  }
`);
