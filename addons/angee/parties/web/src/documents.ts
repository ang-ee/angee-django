// Bespoke console operation for connecting a CardDAV directory — a single action
// that creates the Basic-auth credential + the Directory (two models), the
// sanctioned multi-model-create shape. The directories list/detail are model-driven
// (ResourceList reads the SDL) and sync is the single-id `sync_integration` action, so
// neither needs a document here.

import { graphql, type DocumentType } from "@angee/gql/console";

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

export const PARTY_MERGE_INVALIDATES = [
  "parties.Party",
  "parties.Person",
  "parties.Organization",
  "parties.Handle",
  "parties.CircleMember",
  "parties.Relationship",
  "parties.MergeVeto",
] as const;

export const PartyMergeComparison = graphql(`
  query PartyMergeComparison($left: ID!, $right: ID!) {
    left: parties_by_pk(id: $left) {
      id
      display_name
      notes
      first_met_note
      handles {
        id
        platform
        value
        normalized_value
        label
        is_preferred
      }
      circle_members {
        id
        circle {
          id
          name
        }
      }
      relationships {
        id
      }
      inbound_relationships {
        id
      }
    }
    right: parties_by_pk(id: $right) {
      id
      display_name
      notes
      first_met_note
      handles {
        id
        platform
        value
        normalized_value
        label
        is_preferred
      }
      circle_members {
        id
        circle {
          id
          name
        }
      }
      relationships {
        id
      }
      inbound_relationships {
        id
      }
    }
    left_person: people_by_pk(id: $left) {
      id
      name_prefix
      given_name
      additional_name
      family_name
      name_suffix
      nickname
      birthday
      folder {
        id
        name
      }
    }
    right_person: people_by_pk(id: $right) {
      id
      name_prefix
      given_name
      additional_name
      family_name
      name_suffix
      nickname
      birthday
      folder {
        id
        name
      }
    }
  }
`);

export const DuplicatePartyCandidates = graphql(`
  query DuplicatePartyCandidates($limit: Int = 50) {
    duplicate_party_candidates(limit: $limit) {
      normalized_value
      left {
        id
        display_name
      }
      right {
        id
        display_name
      }
    }
  }
`);

export const PartyReviewCounts = graphql(`
  query PartyReviewCounts {
    party_handles_aggregate(
      where: {
        confidence: { _lt: 0.5 }
        is_confirmed: { _eq: false }
        is_dismissed: { _eq: false }
      }
    ) {
      aggregate {
        count
      }
    }
  }
`);

export const MergeParties = graphql(`
  mutation MergeParties($intoId: ID!, $fromId: ID!, $fieldOverrides: JSON) {
    merge_parties(into_id: $intoId, from_id: $fromId, field_overrides: $fieldOverrides) {
      id
      display_name
    }
  }
`);

export const VetoMerge = graphql(`
  mutation VetoMerge($aId: ID!, $bId: ID!) {
    veto_merge(a_id: $aId, b_id: $bId) {
      id
    }
  }
`);

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

export type MergePartyRecord = NonNullable<
  DocumentType<typeof PartyMergeComparison>["left"]
>;
export type MergePersonRecord = NonNullable<
  DocumentType<typeof PartyMergeComparison>["left_person"]
>;
