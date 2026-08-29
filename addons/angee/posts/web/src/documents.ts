import { graphql, type DocumentType } from "@angee/gql/console";

export const FEED_MESSAGE_MODELS = [
  "messaging.Message",
  "messaging.Reaction",
  "parties.Handle",
  "storage.File",
] as const;

export const FeedMessagesDocument = graphql(`
  query PostsFeedMessages($feedId: String!, $limit: Int!) {
    messages(
      where: { channel: { _eq: $feedId } }
      order_by: [{ sent_at: desc }, { created_at: desc }]
      limit: $limit
    ) {
      id
      preview
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
      parts {
        id
        role
        disposition
        cid
        fragment {
          text
        }
        file {
          id
          filename
          title
          size_bytes
          url
          mime_type {
            mime_type
            label
          }
        }
      }
      reaction_groups {
        reaction
        count
        self_reacted
        handles {
          id
          display_name
          value
        }
      }
    }
  }
`);

export type FeedMessageRow =
  DocumentType<typeof FeedMessagesDocument>["messages"][number];
