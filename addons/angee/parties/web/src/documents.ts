// Bespoke console operation for connecting a CardDAV directory — a single action
// that creates the Basic-auth credential + the Directory (two models), the
// sanctioned multi-model-create shape. The directories list/detail are model-driven
// (ResourceList reads the SDL) and sync is the single-id `sync_integration` action, so
// neither needs a document here.

import { graphql } from "@angee/gql/console";

// Identity decisions: the two verbs of the review flow. Confirming sets full
// confidence + manual source and re-resolves the handle; dismissing writes the
// durable anti-link. Their write blast radius lives here with the verbs;
// `useAuthoredResourceMutation` maps it to both Refine resource invalidations
// and authored-read refetches because this stack has no normalized cache.
export const PARTY_HANDLE_DECISION_INVALIDATES = [
  "parties.PartyHandle",
  "parties.Handle",
  "parties.Party",
  "parties.Person",
] as const;

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
