// Bespoke console operation for connecting a CardDAV directory — a single action
// that creates the Basic-auth credential + the Directory (two models), the
// sanctioned multi-model-create shape. The directories list/detail are model-driven
// (ResourceList reads the SDL) and sync is the single-id `sync_integration` action, so
// neither needs a document here.

import { graphql } from "@angee/gql/console";

// Identity decisions: the two verbs of the review flow. Accepting sets full
// confidence + manual source and re-resolves the handle; dismissing writes the
// durable anti-link. Both return the link so caches update in place.
export const ConfirmPartyHandle = graphql(`
  mutation ConfirmPartyHandle($id: ID!) {
    confirm_party_handle(id: $id) {
      id
      confidence
      source
      is_confirmed
      is_dismissed
    }
  }
`);

export const DismissPartyHandle = graphql(`
  mutation DismissPartyHandle($id: ID!) {
    dismiss_party_handle(id: $id) {
      id
      confidence
      source
      is_confirmed
      is_dismissed
    }
  }
`);

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
