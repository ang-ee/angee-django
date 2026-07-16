import { graphql } from "@angee/gql/console";

// Telegram owns channel creation; pairing and lifecycle actions are generic.
export const ConnectTelegramChannel = graphql(`
  mutation ConnectTelegramChannel(
    $name: String!
    $apiId: String!
    $apiHash: String!
  ) {
    connect_telegram_channel(
      name: $name
      api_id: $apiId
      api_hash: $apiHash
    ) {
      id
      lifecycle
      runtime_status
    }
  }
`);
