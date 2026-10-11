// Bespoke console operation for connecting a CardDAV directory — a single action
// that creates the Basic-auth credential + the Directory (two models), the
// sanctioned multi-model-create shape. The directories list/detail are model-driven
// (ResourceList reads the SDL) and sync is the single-id `sync_integration` action, so
// neither needs a document here.

import { graphql, type DocumentType } from "@angee/gql/console";

export const PartyRecordRedirectDocument = graphql(`
  query PartyRecordRedirect($id: String!) {
    parties_by_pk(id: $id) { id concrete_kind }
  }
`);

export const PartyStatementRedirectDocument = graphql(`
  query PartyStatementRedirect($id: String!) {
    party_statements_by_pk(id: $id) {
      id
      subject { id }
    }
  }
`);

// Identity decisions write preferred person statements and re-resolve the handle.
// A dismissal is the
// durable anti-link. Their write blast radius lives here with the verbs;
// `useAuthoredResourceMutation` maps it to both Refine resource invalidations
// and authored-read refetches because this stack has no normalized cache.
export const PARTY_STATEMENT_DECISION_INVALIDATES = [
  "parties.PartyStatement",
  "parties.Handle",
  "parties.Party",
  "parties.Person",
  "parties.Organization",
] as const;

export const PARTY_MERGE_INVALIDATES = [
  "parties.Party",
  "parties.Person",
  "parties.Organization",
  "parties.Handle",
  "parties.PartyStatement",
  "parties.PersonStatement",
  "parties.OrganizationStatement",
  "parties.Address",
  "parties.AddressStatement",
  "parties.RelationshipStatement",
  "parties.CircleMember",
  "parties.Relationship",
  "parties.PartySameAsPairStatement",
] as const;

export const CIRCLE_MEMBER_INVALIDATES = [
  "parties.CircleMember",
  "parties.Circle",
  "parties.Party",
  "parties.Person",
] as const;

export const PeopleWorkbench = graphql(`
  query PeopleWorkbench($scope: PeopleWorkbenchScope!, $circle: ID) {
    people_workbench(scope: $scope, circle: $circle) {
      all_count
      unassigned_count
      to_review_count
      filtered_ids
      truncated
      circles {
        id
        name
        icon
        member_count
        parent {
          id
        }
      }
    }
  }
`);

export const AddCircleMember = graphql(`
  mutation AddCircleMember($circle: ID!, $party: ID!) {
    insert_circle_members_one(object: { circle: $circle, party: $party }) {
      id
    }
  }
`);

export const RemoveCircleMember = graphql(`
  mutation RemoveCircleMember($id: String!) {
    delete_circle_members_by_pk(id: $id) {
      id
    }
  }
`);

export const PartiesOverview = graphql(`
  query PartiesOverview($duplicateLimit: Int = 100) {
    contacts: people_aggregate {
      aggregate {
        count
      }
    }
    organizations: organizations_aggregate {
      aggregate {
        count
      }
    }
    unresolved_handles: handles_aggregate(where: { party: { _is_null: true } }) {
      aggregate {
        count
      }
    }
    review_queue: party_statements_aggregate(
      where: {
        to_review: { _eq: true }
      }
    ) {
      aggregate {
        count
      }
    }
    same_as_pairs(limit: $duplicateLimit) {
      left {
        id
      }
      right {
        id
      }
    }
  }
`);

export const PartyMergeComparison = graphql(`
  query PartyMergeComparison($left: String!, $right: String!) {
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
    }
  }
`);

export const SameAsPairs = graphql(`
  query SameAsPairs($limit: Int = 50) {
    same_as_pairs(limit: $limit) {
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
    party_statements_aggregate(
      where: {
        to_review: { _eq: true }
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

export const DismissSameAs = graphql(`
  mutation DismissSameAs($aId: ID!, $bId: ID!) {
    dismiss_same_as(a_id: $aId, b_id: $bId) {
      id
    }
  }
`);

export const ConfirmPartyStatement = graphql(`
  mutation ConfirmPartyStatement($id: ID!) {
    confirm_party_statement(id: $id) {
      id
      confidence
      asserted_by
      is_confirmed
      is_dismissed
    }
  }
`);

export const DismissPartyStatement = graphql(`
  mutation DismissPartyStatement($id: ID!) {
    dismiss_party_statement(id: $id) {
      id
      confidence
      asserted_by
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
