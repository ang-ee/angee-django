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
        ...MessageSenderFields
      }
      parts {
        ...MessagePartFields
      }
      reaction_groups {
        ...ReactionGroupFields
      }
    }
  }
`);

export type FeedMessageRow =
  DocumentType<typeof FeedMessagesDocument>["messages"][number];
