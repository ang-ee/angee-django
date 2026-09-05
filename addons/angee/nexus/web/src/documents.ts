// Relationship intelligence uses authored reads for the bounded graph, timeline
// scopes, and overview rollups. Model-driven Tie/Cadence CRUD remains on the
// generated resources.

import { graphql } from "@angee/gql/console";

export const NexusTimelineMessageFields = graphql(`
  fragment NexusTimelineMessageFields on MessageType {
    id
    feed_order_key
    preview
    platform
    direction
    sent_at
    created_at
    sender {
      id
      display_name
      value
      party_link_confirmed
      party {
        display_name
      }
    }
    thread {
      id
      title {
        text
      }
    }
  }
`);

export const NexusTimeline = graphql(`
  query NexusTimeline(
    $partyId: ID!
    $circleId: ID!
    $circle: Boolean!
    $beforeCursor: String
    $throughCursor: String
    $limit: Int!
    $search: String!
  ) {
    party_message_feed(
      party_id: $partyId
      before_cursor: $beforeCursor
      through_cursor: $throughCursor
      limit: $limit
      search: $search
    ) @skip(if: $circle) {
      count
      older_cursor
      has_older
      has_more_in_window
      has_older_than_through
      messages {
        ...NexusTimelineMessageFields
      }
    }
    circle_message_feed(
      circle_id: $circleId
      before_cursor: $beforeCursor
      through_cursor: $throughCursor
      limit: $limit
      search: $search
    ) @include(if: $circle) {
      count
      older_cursor
      has_older
      has_more_in_window
      has_older_than_through
      messages {
        ...NexusTimelineMessageFields
      }
    }
  }
`);

export const NexusTimelineRevalidate = graphql(`
  query NexusTimelineRevalidate(
    $partyId: ID!
    $circleId: ID!
    $circle: Boolean!
    $search: String!
    $ids: [ID!]!
  ) {
    party_message_feed_revalidate(party_id: $partyId, search: $search, ids: $ids) @skip(if: $circle) {
      messages {
        ...NexusTimelineMessageFields
      }
      absent_ids
    }
    circle_message_feed_revalidate(circle_id: $circleId, search: $search, ids: $ids) @include(if: $circle) {
      messages {
        ...NexusTimelineMessageFields
      }
      absent_ids
    }
  }
`);

export const NexusPartyGraph = graphql(`
  query NexusPartyGraph(
    $rootId: ID
    $circleId: ID
    $lenses: [String!]
    $depth: Int!
    $limit: Int!
  ) {
    party_graph(
      root_id: $rootId
      circle_id: $circleId
      lenses: $lenses
      depth: $depth
      limit: $limit
    ) {
      truncated
      nodes
      edges
    }
  }
`);

export const NexusGraphParties = graphql(`
  query NexusGraphParties($limit: Int!) {
    parties(order_by: [{ display_name: asc }], limit: $limit) {
      id
      display_name
    }
    circles(order_by: [{ position: asc }, { name: asc }], limit: $limit) {
      id
      name
    }
  }
`);

export const NexusNetworkPane = graphql(`
  query NexusNetworkPane($rootId: ID!) {
    party_graph(root_id: $rootId, lenses: ["ego"], depth: 1, limit: 20) {
      nodes
      edges
      truncated
    }
  }
`);

export const NexusOverview = graphql(`
  query NexusOverview($peekLimit: Int!) {
    nexus_overview(peek_limit: $peekLimit) {
      fading_count
      due_count
      fading_ties {
        id
        gravity
        last_interaction_at
        party_a {
          id
          display_name
        }
        party_b {
          id
          display_name
        }
      }
      due_cadences {
        id
        cadence_days
        touch_due_at
        party {
          id
          display_name
        }
      }
    }
  }
`);
