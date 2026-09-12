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
    starred
    sender {
      ...MessageSenderFields
    }
    thread {
      id
      title {
        text
      }
      modality
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
        }
        thread {
          id
          title {
            text
          }
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
        message {
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
        message {
          ...InboxMessageFields
        }
        part {
          ...MessagePartFields
        }
        handle {
          ...MessageSenderFields
          platform
        }
        src {
          ...InboxMessageFields
        }
        dst {
          ...InboxMessageFields
        }
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
        received_at
        external_id
        parts {
          ...MessagePartFields
        }
        participants {
          id
          role
          handle {
            ...MessageSenderFields
          }
        }
        reaction_groups {
          ...ReactionGroupFields
        }
      }
    }
  }
`);

export const SetInboxStar = graphql(`
  mutation NexusSetInboxStar($id: ID!, $starred: Boolean!) {
    set_inbox_message_starred(id: $id, starred: $starred) {
      id
      starred
    }
  }
`);

export type InboxMessageRow = DocumentType<typeof InboxMessageFields>;
export type InboxMessageDetail = DocumentType<
  typeof InboxMessage
>["inbox_message"]["message"];
export type InboxNavigatorRow = DocumentType<
  typeof InboxNavigator
>["inbox_navigator"]["rows"][number];
export type InboxResultRow = DocumentType<
  typeof InboxResults
>["inbox_results"]["rows"][number];
export type InboxConnectionRow = DocumentType<
  typeof InboxConnections
>["inbox_connections"]["rows"][number];

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
        title {
          text
        }
      }
      count
      total
      visible
      previous
      next
    }
  }
`);
