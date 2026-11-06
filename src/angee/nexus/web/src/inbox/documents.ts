import { graphql, type DocumentType } from "@angee/gql/console";

export const InboxMessageFields = graphql(`
  fragment InboxMessageFields on MessageType {
    id
    preview
    title
    platform
    direction
    status
    sent_at
    created_at
    sender {
      ...MessageSenderFields
    }
    thread {
      id
      modality
      title {
        text
      }
    }
    channel {
      id
      display_name
    }
  }
`);

export const InboxAccounts = graphql(`
  query NexusInboxAccounts {
    inbox_accounts {
      id
      display_name
    }
    inbox_platforms
    inbox_relation_kinds
  }
`);

export const InboxNavigator = graphql(`
  query NexusInboxNavigator(
    $coverage: InboxCoverageInput
    $options: InboxNavigatorInput
    $scope: InboxGroupScopeInput
    $parent: String
    $page: Int!
    $size: Int!
  ) {
    inbox_navigator(
      coverage: $coverage
      options: $options
      scope: $scope
      parent: $parent
      page: $page
      size: $size
    ) {
      count
      message_count
      rows {
        id
        label
        count
        latest
        first
        preview
        parent_id
        has_children
        handle {
          ...MessageSenderFields
          platform
        }
        party {
          id
          display_name
          display_avatar { url }
        }
        thread {
          id
        }
        circle {
          id
          name
        }
      }
    }
  }
`);

export const InboxNavigatorGroups = graphql(`
  query NexusInboxNavigatorGroups(
    $axis: String!
    $coverage: InboxCoverageInput
    $options: InboxNavigatorInput
    $page: Int!
    $size: Int!
  ) {
    inbox_navigator_groups(
      axis: $axis
      coverage: $coverage
      options: $options
      page: $page
      size: $size
    ) {
      count
      record_count
      message_count
      rows {
        value
        label
        count
        message_count
      }
    }
  }
`);

export const InboxResults = graphql(`
  query NexusInboxResults(
    $coverage: InboxCoverageInput
    $search: InboxSearchInput
    $options: InboxResultInput
    $sender: String!
    $circle: String!
    $scope: InboxGroupScopeInput
    $page: Int!
    $size: Int!
  ) {
    inbox_results(
      coverage: $coverage
      search: $search
      options: $options
      sender: $sender
      circle: $circle
      scope: $scope
      page: $page
      size: $size
    ) {
      count
      message_count
      rows {
        id
        latest
        count
        part_count
        message_id
        part {
          ...MessagePartFields
        }
        file {
          id
          filename
          url
          size_bytes
          mime_type {
            mime_type
          }
        }
        fragment {
          id
          text
        }
      }
      messages {
        ...InboxMessageFields
      }
    }
  }
`);

export const InboxResultGroups = graphql(`
  query NexusInboxResultGroups(
    $axis: String!
    $coverage: InboxCoverageInput
    $search: InboxSearchInput
    $options: InboxResultInput
    $sender: String!
    $circle: String!
    $page: Int!
    $size: Int!
  ) {
    inbox_result_groups(
      axis: $axis
      coverage: $coverage
      search: $search
      options: $options
      sender: $sender
      circle: $circle
      page: $page
      size: $size
    ) {
      count
      record_count
      message_count
      rows {
        value
        label
        count
        message_count
        latest
        platform
        modality
        account
      }
    }
  }
`);

export const InboxRelatedTarget = graphql(`
  query NexusInboxRelatedTarget($target: String!, $source: String!) {
    inbox_related_target(target: $target, source: $source) {
      id
      kind
      label
      count
      unit
      source {
        ...InboxMessageFields
      }
      part {
        ...MessagePartFields
      }
      file {
        id
        filename
        url
        size_bytes
        mime_type {
          mime_type
        }
      }
      fragment {
        id
        text
      }
    }
  }
`);

export const InboxConnectionsCount = graphql(`
  query NexusInboxConnectionsCount(
    $target: String!
    $text: String!
    $oldest: Boolean!
    $page: Int!
    $size: Int!
  ) {
    inbox_connections(target: $target, text: $text, oldest: $oldest, page: $page, size: $size) {
      message_count
    }
  }
`);

export const InboxConnections = graphql(`
  query NexusInboxConnections(
    $target: String!
    $text: String!
    $oldest: Boolean!
    $page: Int!
    $size: Int!
  ) {
    inbox_connections(
      target: $target
      text: $text
      oldest: $oldest
      page: $page
      size: $size
    ) {
      count
      message_count
      rows {
        id
        label
        role
        provenance
        confidence
        message_id
        part {
          ...MessagePartFields
        }
        handle {
          ...MessageSenderFields
          platform
        }
        src_id
        dst_id
      }
      messages {
        ...InboxMessageFields
      }
    }
  }
`);

export const InboxMessage = graphql(`
  query NexusInboxMessage(
    $id: ID!
    $coverage: InboxCoverageInput
    $search: InboxSearchInput
    $sender: String!
    $circle: String!
  ) {
    inbox_message(
      id: $id
      coverage: $coverage
      search: $search
      sender: $sender
      circle: $circle
    ) {
      matches
      uses {
        part_id
        target
        count
      }
      message {
        ...InboxMessageFields
        ...MessageReaderFields
        thread {
          id
          conversation_label
        }
        received_at
        external_id
      }
    }
  }
`);

export type InboxNavigatorRow = DocumentType<
  typeof InboxNavigator
>["inbox_navigator"]["rows"][number];
type InboxResultsPage = DocumentType<typeof InboxResults>["inbox_results"];
type InboxConnectionsPage = DocumentType<typeof InboxConnections>["inbox_connections"];
/** A message a result or connection page carries once for every row that names it. */
export type InboxPageMessage = InboxResultsPage["messages"][number];
export type InboxResultRow = Omit<InboxResultsPage["rows"][number], "message_id"> & {
  message: InboxPageMessage;
};
export type InboxConnectionRow = Omit<
  InboxConnectionsPage["rows"][number],
  "message_id" | "src_id" | "dst_id"
> & {
  message: InboxPageMessage | null;
  src: InboxPageMessage | null;
  dst: InboxPageMessage | null;
};

export const InboxSelection = graphql(`
  query NexusInboxSelection($sender: String!, $circle: String!) {
    inbox_selection(sender: $sender, circle: $circle) {
      id
      label
      party {
        id
        display_name
      }
      handle {
        ...MessageSenderFields
      }
      circle {
        id
        name
      }
    }
  }
`);

export const InboxTranscriptMatches = graphql(`
  query NexusInboxTranscriptMatches(
    $thread: String!
    $visible: [String!]!
    $at: String!
    $coverage: InboxCoverageInput
    $search: InboxSearchInput
    $sender: String!
    $circle: String!
  ) {
    inbox_transcript_matches(
      thread: $thread
      visible: $visible
      at: $at
      coverage: $coverage
      search: $search
      sender: $sender
      circle: $circle
    ) {
      thread {
        id
        conversation_label
        modality
      }
      count
      total
      visible
      previous
      next
    }
  }
`);
