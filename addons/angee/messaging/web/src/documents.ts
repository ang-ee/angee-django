import { graphql, type DocumentType } from "@angee/gql/console";

// The messaging models a record thread reads: the live-refresh keys for the
// thread/activity queries and the invalidation set every chatter mutation
// republishes. One owner, shared by both chatter panes.
export const READ_MODELS = [
  "messaging.Thread",
  "messaging.Message",
  "messaging.ThreadFollower",
  "messaging.ThreadActivity",
  "messaging.ThreadNotification",
  "messaging.Reaction",
  "messaging.MessageStar",
] as const;

// One owner for the record-feed message selection: the four operations that
// return a full chatter message (the thread read + post/update/delete payloads)
// spread this fragment instead of repeating the field set. `can_edit`/`can_delete`
// are server-resolved capabilities (rebac + mail rules) the feed reads directly —
// never a client heuristic. The client-preset resolves the fragment by name.
export const RecordMessageFields = graphql(`
  fragment RecordMessageFields on MessageType {
    id
    title
    preview
    direction
    status
    starred
    needaction
    message_type
    can_edit
    can_delete
    sender {
      id
      display_name
      value
    }
    parent {
      id
      preview
      message_type
      subtype {
        key
        name
        description
      }
    }
    subtype {
      key
      name
      description
    }
    sent_at
    created_at
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
    tracking_values {
      id
      position
      field_name
      field_label
      old_display
      new_display
    }
    parts {
      role
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
  }
`);

// The channel conversation transcript reads the thread's messages straight off
// the `messages` auto-CRUD resource — a fixed-size page ordered newest-first,
// keyset-paginated on the `(sent_at, created_at)` cursor: "load older" passes the
// oldest loaded row's timestamps instead of growing a re-fetched window, so a
// million-message thread pages in constant work per fetch (the Zulip/Synapse
// anchor-pagination shape, never OFFSET). `messages_aggregate` reports the thread
// total so the view knows when older messages remain. Only the fields a
// `ChatBubble` transcript renders are selected.
export const TranscriptMessageFields = graphql(`
  fragment TranscriptMessageFields on MessageType {
    id
    direction
    title
    preview
    message_type
    sent_at
    created_at
    sender {
      id
      display_name
      value
    }
    parts {
      role
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
`);

export const ThreadTranscriptDocument = graphql(`
  query MessagingThreadTranscript($threadId: String!, $limit: Int!) {
    messages(
      where: { thread: { _eq: $threadId } }
      order_by: [{ sent_at: desc }, { created_at: desc }]
      limit: $limit
    ) {
      ...TranscriptMessageFields
    }
    messages_aggregate(where: { thread: { _eq: $threadId } }) {
      aggregate {
        count
      }
    }
  }
`);

// "Load older" keyset page: before the oldest loaded row's (sent_at,
// created_at) cursor, boundary-INCLUSIVE on created_at — rows tying the anchor
// on both timestamps are refetched and the client's id-keyed archive dedups
// the overlap, so a tie at the page cut can never be skipped (ids are opaque
// sqids, so they cannot serve as the third cursor key server-side). Constant
// work per fetch however deep the history — never OFFSET, never a growing
// re-fetched window.
export const ThreadTranscriptOlderDocument = graphql(`
  query MessagingThreadTranscriptOlder(
    $threadId: String!
    $limit: Int!
    $beforeSentAt: DateTime!
    $beforeCreatedAt: DateTime!
  ) {
    messages(
      where: {
        _and: [
          { thread: { _eq: $threadId } }
          {
            _or: [
              { sent_at: { _lt: $beforeSentAt } }
              {
                _and: [
                  { sent_at: { _eq: $beforeSentAt } }
                  { created_at: { _lte: $beforeCreatedAt } }
                ]
              }
            ]
          }
        ]
      }
      order_by: [{ sent_at: desc }, { created_at: desc }]
      limit: $limit
    ) {
      ...TranscriptMessageFields
    }
  }
`);

// The recipient picker's "add anyone" catalogue. Member-scoped: the IAM
// `colleagues` surface returns the active users who share a company of record with
// the actor, so a plain member (not just a platform admin) can address a comment to
// a co-worker. The admin-only `users` catalogue is out of reach here by design.
export const MessagingRecipientUsersDocument = graphql(`
  query MessagingRecipientUsers($limit: Int = 100) {
    colleagues(limit: $limit) {
      id
      username
      display_name
      email
      is_active
    }
  }
`);

export const RecordThreadDocument = graphql(`
  query MessagingRecordThread(
    $modelLabel: String!
    $recordId: ID!
    $search: String = ""
    $messageLimit: Int = 50
    $before: ID = null
    $after: ID = null
    $around: ID = null
  ) {
    record_thread(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        search: $search
        message_limit: $messageLimit
        before: $before
        after: $after
        around: $around
      }
    ) {
      error
      error_code
      thread {
        id
        title {
          text
        }
        message_count
        last_message_at
      }
      message_result_count
      messages {
        ...RecordMessageFields
      }
      follower_count
      is_following
      self_follower {
        id
        notification_policy
        subtype_keys
        user {
          id
          username
          display_name
        }
      }
      suggested_recipients {
        reason
        source
        user {
          id
          username
          display_name
          email
          is_active
        }
      }
      subtypes {
        key
        name
        description
        internal
        default
      }
      unread_count
      needaction_count
      message_has_error
      message_has_error_counter
      attachment_count
      notifications {
        id
        notification_type
        notification_status
        message {
          id
          preview
        }
      }
      followers {
        id
        notification_policy
        subtype_keys
        user {
          id
          username
          display_name
        }
      }
      activity_count
      activities {
        id
        activity_type
        summary
        note
        due_date
        completed_at
        feedback
        status
        state
        user {
          id
          username
          display_name
        }
      }
    }
  }
`);

export const PostRecordMessageDocument = graphql(`
  mutation MessagingPostRecordMessage(
    $modelLabel: String!
    $recordId: ID!
    $body: String!
    $kind: String = "comment"
    $parentMessageId: ID = null
    $attachmentIds: [ID!] = []
    $recipientUserIds: [ID!] = []
    $autofollowRecipients: Boolean = false
  ) {
    post_record_message(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        body: $body
        kind: $kind
        parent_message_id: $parentMessageId
        attachment_ids: $attachmentIds
        recipient_user_ids: $recipientUserIds
        autofollow_recipients: $autofollowRecipients
      }
    ) {
      error
      error_code
      follower_count
      is_following
      unread_count
      needaction_count
      message_has_error
      message_has_error_counter
      activity_count
      attachment_count
      message {
        ...RecordMessageFields
      }
      thread {
        id
        title {
          text
        }
        message_count
        last_message_at
      }
    }
  }
`);

export const UpdateRecordMessageDocument = graphql(`
  mutation MessagingUpdateRecordMessage(
    $modelLabel: String!
    $recordId: ID!
    $messageId: ID!
    $body: String!
  ) {
    update_record_message(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        message_id: $messageId
        body: $body
      }
    ) {
      error
      error_code
      follower_count
      is_following
      unread_count
      needaction_count
      message_has_error
      message_has_error_counter
      activity_count
      attachment_count
      message {
        ...RecordMessageFields
      }
      thread {
        id
        title {
          text
        }
        message_count
        last_message_at
      }
    }
  }
`);

export const DeleteRecordMessageDocument = graphql(`
  mutation MessagingDeleteRecordMessage(
    $modelLabel: String!
    $recordId: ID!
    $messageId: ID!
  ) {
    delete_record_message(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        message_id: $messageId
      }
    ) {
      error
      error_code
      deleted_message_id
      message_result_count
      follower_count
      is_following
      unread_count
      needaction_count
      message_has_error
      message_has_error_counter
      activity_count
      attachment_count
      thread {
        id
        title {
          text
        }
        message_count
        last_message_at
      }
      messages {
        ...RecordMessageFields
      }
    }
  }
`);

export const SetRecordMessageReactionDocument = graphql(`
  mutation MessagingSetRecordMessageReaction(
    $modelLabel: String!
    $recordId: ID!
    $messageId: ID!
    $reaction: String!
    $action: String = "toggle"
  ) {
    set_record_message_reaction(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        message_id: $messageId
        reaction: $reaction
        action: $action
      }
    ) {
      error
      error_code
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
      message {
        id
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
  }
`);

export const SetRecordMessageStarredDocument = graphql(`
  mutation MessagingSetRecordMessageStarred(
    $modelLabel: String!
    $recordId: ID!
    $messageId: ID!
    $starred: Boolean = null
  ) {
    set_record_message_starred(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        message_id: $messageId
        starred: $starred
      }
    ) {
      error
      error_code
      starred
      message {
        id
        starred
      }
    }
  }
`);

export const MarkRecordThreadReadDocument = graphql(`
  mutation MessagingMarkRecordThreadRead($modelLabel: String!, $recordId: ID!) {
    mark_record_thread_read(
      input: {
        model_label: $modelLabel
        record_id: $recordId
      }
    ) {
      error
      error_code
      unread_count
      needaction_count
    }
  }
`);

export const MarkRecordMessageDoneDocument = graphql(`
  mutation MessagingMarkRecordMessageDone(
    $modelLabel: String!
    $recordId: ID!
    $messageId: ID!
  ) {
    mark_record_message_done(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        message_id: $messageId
      }
    ) {
      error
      error_code
      unread_count
      needaction_count
      message {
        id
        needaction
      }
      thread {
        id
        title {
          text
        }
        message_count
        last_message_at
      }
    }
  }
`);

export const SetRecordFollowingDocument = graphql(`
  mutation MessagingSetRecordFollowing(
    $modelLabel: String!
    $recordId: ID!
    $following: Boolean!
    $notificationPolicy: String = "inbox"
    $subtypeKeys: [String!] = []
  ) {
    set_record_following(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        following: $following
        notification_policy: $notificationPolicy
        subtype_keys: $subtypeKeys
      }
    ) {
      error
      error_code
      follower_count
      is_following
      follower {
        id
        notification_policy
        subtype_keys
      }
    }
  }
`);

export const ScheduleRecordActivityDocument = graphql(`
  mutation MessagingScheduleRecordActivity(
    $modelLabel: String!
    $recordId: ID!
    $summary: String!
    $note: String = ""
    $dueDate: Date
    $activityType: String = "todo"
  ) {
    schedule_record_activity(
      input: {
        model_label: $modelLabel
        record_id: $recordId
        summary: $summary
        note: $note
        due_date: $dueDate
        activity_type: $activityType
      }
    ) {
      error
      error_code
      activity_count
      activity {
        id
        activity_type
        summary
        note
        due_date
        completed_at
        feedback
        status
        state
        user {
          id
          username
          display_name
        }
      }
    }
  }
`);

export const CompleteRecordActivityDocument = graphql(`
  mutation MessagingCompleteRecordActivity(
    $activityId: ID!
    $feedback: String = ""
  ) {
    complete_record_activity(
      input: {
        activity_id: $activityId
        feedback: $feedback
      }
    ) {
      error
      error_code
      activity_count
      activity {
        id
        summary
        completed_at
        feedback
        status
        state
      }
    }
  }
`);

export const CancelRecordActivityDocument = graphql(`
  mutation MessagingCancelRecordActivity($activityId: ID!) {
    cancel_record_activity(input: { activity_id: $activityId }) {
      error
      error_code
      activity_count
      activity {
        id
        summary
        completed_at
        status
        state
      }
    }
  }
`);

// The activity tab reads only the scheduled activities, not the full message
// feed: its own narrow window off `record_thread` so opening Activity never pulls
// the messages / followers / recipients payload the Comments tab needs.
export const RecordActivityThreadDocument = graphql(`
  query MessagingRecordActivityThread($modelLabel: String!, $recordId: ID!) {
    record_thread(input: { model_label: $modelLabel, record_id: $recordId }) {
      error
      error_code
      activity_count
      activities {
        id
        activity_type
        summary
        note
        due_date
        completed_at
        feedback
        status
        state
        user {
          id
          username
          display_name
        }
      }
    }
  }
`);

export type ThreadTranscriptRow =
  DocumentType<typeof ThreadTranscriptDocument>["messages"][number];

export type RecordThreadPayload = DocumentType<typeof RecordThreadDocument>["record_thread"];
export type RecordActivityThreadPayload =
  DocumentType<typeof RecordActivityThreadDocument>["record_thread"];
export type RecordMessageRow = NonNullable<RecordThreadPayload["messages"]>[number];
export type RecordActivityRow = NonNullable<RecordActivityThreadPayload["activities"]>[number];
export type RecordNotificationRow = NonNullable<RecordThreadPayload["notifications"]>[number];
export type SuggestedRecipientRow =
  NonNullable<RecordThreadPayload["suggested_recipients"]>[number];
export type RecipientUserRow =
  NonNullable<DocumentType<typeof MessagingRecipientUsersDocument>["colleagues"]>[number];
