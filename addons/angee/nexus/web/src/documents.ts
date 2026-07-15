// The person timeline is an authored read: one keyset page of the cross-channel
// feed exchanged with a party (newest-first pages; `before` is the oldest loaded
// message's id). The ties surface is model-driven and needs no document.

import { graphql } from "@angee/gql/console";

export const PartyTimeline = graphql(`
  query PartyTimeline($partyId: ID!, $before: ID, $limit: Int!, $search: String!) {
    party_timeline(party_id: $partyId, before: $before, limit: $limit, search: $search) {
      count
      messages {
        id
        preview
        platform
        direction
        sent_at
        created_at
        sender {
          id
          display_name
          value
        }
        thread {
          id
          title {
            text
          }
        }
      }
    }
  }
`);
