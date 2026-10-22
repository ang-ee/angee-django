// Authored reads for the bounded graph and overview rollups.
// Tie/Cadence CRUD uses the generated resources.

import { graphql } from "@angee/gql/console";

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
