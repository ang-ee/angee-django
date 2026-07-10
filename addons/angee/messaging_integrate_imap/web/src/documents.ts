import { graphql } from "@angee/gql/console";

// The IMAP bridge owns this bespoke operation because it creates a Basic-auth
// credential and a channel together. The base channel list/detail stay
// model-driven, and sync is still the generic integration action.
export const ConnectImapChannel = graphql(`
  mutation ConnectImapChannel(
    $name: String!
    $host: String!
    $security: String!
    $port: Int
    $username: String!
    $password: String!
    $mailboxes: [String!]
    $ownAddresses: [String!]
  ) {
    connect_imap_channel(
      name: $name
      host: $host
      security: $security
      port: $port
      username: $username
      password: $password
      mailboxes: $mailboxes
      own_addresses: $ownAddresses
    ) {
      id
      lifecycle
      runtime_status
    }
  }
`);
