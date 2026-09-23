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

export const PreviewImapSample = graphql(`
  mutation PreviewImapSample(
    $id: ID!
    $mailbox: String!
    $since: Date
    $before: Date
    $allDates: Boolean!
    $uidvalidity: Int
    $upperUid: Int
    $beforeUid: Int
    $limit: Int!
  ) {
    preview_imap_sample(
      id: $id
      mailbox: $mailbox
      since: $since
      before: $before
      all_dates: $allDates
      uidvalidity: $uidvalidity
      upper_uid: $upperUid
      before_uid: $beforeUid
      limit: $limit
    ) {
      mailbox
      uidvalidity
      upper_uid
      total_count
      next_before_uid
      truncated
      messages { uid subject sent_at sender size flags }
    }
  }
`);

export const ImportImapSample = graphql(`
  mutation ImportImapSample($id: ID!, $mailbox: String!, $uidvalidity: Int!, $uids: [Int!]!) {
    import_imap_sample(id: $id, mailbox: $mailbox, uidvalidity: $uidvalidity, uids: $uids) {
      message_ids
      requested_uids
      imported_uids
      missing_uids
      flags_unchanged
    }
  }
`);
