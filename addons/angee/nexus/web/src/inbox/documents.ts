import { graphql, type DocumentType } from "@angee/gql/console";

export const InboxMessageFields = graphql(`
  fragment InboxMessageFields on MessageType {
    id preview title platform direction status sent_at created_at starred
    sender { ...MessageSenderFields }
    thread { id title { text } modality }
    channel { id display_name }
  }
`);

export const InboxAccounts = graphql(`
  query NexusInboxAccounts { inbox_accounts { id display_name } inbox_platforms }
`);

export const InboxSenders = graphql(`
  query NexusInboxSenders($coverage: InboxCoverageInput, $text: String!, $includeSent: Boolean!, $sort: String!, $link: String!, $page: Int!) {
    inbox_senders(coverage: $coverage, text: $text, include_sent: $includeSent, sort: $sort, link: $link, page: $page) {
      count
      rows { id count latest first preview handle { ...MessageSenderFields platform } party { id display_name } }
    }
  }
`);

export const InboxConversations = graphql(`
  query NexusInboxConversations($coverage: InboxCoverageInput, $search: InboxSearchInput, $sender: String!, $circle: String!, $page: Int!, $oldest: Boolean!) {
    inbox_conversations(coverage: $coverage, search: $search, sender: $sender, circle: $circle, page: $page, oldest: $oldest) {
      count message_count
      rows { id latest matching_count total_count thread { id title { text } modality } messages { ...InboxMessageFields } }
    }
  }
`);

export const InboxMessage = graphql(`
  query NexusInboxMessage($id: ID!, $coverage: InboxCoverageInput, $search: InboxSearchInput, $sender: String!, $circle: String!) {
    inbox_message(id: $id, coverage: $coverage, search: $search, sender: $sender, circle: $circle) {
      matches
      uses { part_id target count }
      message {
        ...InboxMessageFields
        received_at external_id
        parts { ...MessagePartFields }
        participants { id role handle { ...MessageSenderFields } }
        reaction_groups { ...ReactionGroupFields }
      }
    }
  }
`);

export const InboxRelated = graphql(`
  query NexusInboxRelated($target: String!, $text: String!, $page: Int!, $oldest: Boolean!) {
    inbox_related(target: $target, text: $text, page: $page, oldest: $oldest) {
      count message_count rows { ...InboxMessageFields parts { ...MessagePartFields } }
    }
  }
`);

export const SetInboxStar = graphql(`
  mutation NexusSetInboxStar($id: ID!, $starred: Boolean!) {
    set_inbox_message_starred(id: $id, starred: $starred) { id starred }
  }
`);

export type InboxMessageRow = DocumentType<typeof InboxMessageFields>;
export type InboxConversationRow = DocumentType<typeof InboxConversations>["inbox_conversations"]["rows"][number];
export type InboxSenderRow = DocumentType<typeof InboxSenders>["inbox_senders"]["rows"][number];
export type InboxMessageDetail = DocumentType<typeof InboxMessage>["inbox_message"]["message"];
