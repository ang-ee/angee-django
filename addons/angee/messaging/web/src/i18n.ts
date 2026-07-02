// English message bundle for the `messaging` namespace. Components resolve these
// through `useMessagingT()` (below); the addon manifest contributes the bundle
// under `i18n.messaging`. Keys are dotted by surface. Metadata-driven field/column
// labels live in the SDL, not here — only bespoke component copy is routed.

import { createNamespaceT } from "@angee/ui";

export const enMessagingMessages: Record<string, string> = {
  // RecordChatterPane — the Comments chatter tab.
  "chatter.noRecord": "No record selected",
  "chatter.noRecordHint": "Open a record to discuss it.",
  "chatter.loading": "Loading comments",
  "chatter.disabled": "Comments are not enabled",
  "chatter.disabledHint": "This record has no model chatter thread.",
  "chatter.following": "{count} following",
  "chatter.unread": "{count} unread",
  "chatter.failed": "{count} failed",
  "chatter.markRead": "Mark read",
  "chatter.follow": "Follow",
  "chatter.unfollow": "Unfollow",
  "chatter.search": "Search comments",
  "chatter.feedLabel": "Comments",
  "chatter.results": "{count} results",
  "chatter.emptyTitle": "No comments yet",
  "chatter.emptyHint": "Start the discussion from this record.",
  "chatter.noMatchTitle": "No matching comments",
  "chatter.noMatchHint": "Try a different search.",
  "chatter.editedMeta": "edited",

  // Message row affordances.
  "message.author": "Someone",
  "message.reactions": "Reactions",
  "message.markDone": "Mark message done",
  "message.reply": "Reply to message",
  "message.star": "Star message",
  "message.unstar": "Unstar message",
  "message.addReaction": "Add reaction",
  "message.edit": "Edit comment",
  "message.delete": "Delete message",
  "message.editLabel": "Edit comment",
  "message.cancel": "Cancel",
  "message.save": "Save",
  "message.replyingTo": "Replying to {kind}",
  "message.kindNote": "internal note",
  "message.kindUpdate": "update",
  "message.kindMessage": "message",
  "message.directionInbound": "Inbound",
  "message.directionOutbound": "Outbound",

  // Composer.
  "composer.comment": "Comment",
  "composer.note": "Note",
  "composer.cancelReply": "Cancel reply",
  "composer.recipients": "Recipients",
  "composer.addRecipient": "Add recipient",
  "composer.loadingRecipients": "Loading recipients",
  "composer.noRecipients": "No recipients",
  "composer.followRecipients": "Follow",
  "composer.suggested": "Suggested",
  "composer.follower": "Follower",
  "composer.removeRecipient": "Remove {name}",
  "composer.writeComment": "Write a comment",
  "composer.logNote": "Log an internal note",
  "composer.messageLabel": "Message",
  "composer.attach": "Attach files",
  "composer.send": "Send",
  "composer.log": "Log",
  "composer.dropFiles": "Drop files to attach",
  "composer.removeAttachment": "Remove {name}",
  "composer.clearUploads": "Clear finished uploads",

  // Upload task states.
  "upload.preparing": "Preparing",
  "upload.uploading": "Uploading",
  "upload.finalizing": "Finalizing",
  "upload.failed": "Failed",
  "upload.attached": "Attached",

  // Follow subtypes.
  "subtype.legend": "Notification types",

  // Chatter transport / mutation errors.
  "error.generic": "Something went wrong. Try again.",
  "error.postComment": "Could not post comment.",
  "error.postNote": "Could not post note.",
  "error.update": "Could not update comment.",
  "error.delete": "Could not delete message.",
  "error.reaction": "Could not update reaction.",
  "error.starred": "Could not update starred message.",
  "error.following": "Could not update followers.",
  "error.filters": "Could not update notification filters.",
  "error.markRead": "Could not mark comments read.",
  "error.markDone": "Could not mark message done.",

  // ThreadTranscript — the channel conversation transcript on the Thread detail.
  "transcript.tab": "Conversation",
  "transcript.label": "Conversation transcript",
  "transcript.loading": "Loading conversation",
  "transcript.error": "Could not load the conversation.",
  "transcript.emptyTitle": "No messages yet",
  "transcript.emptyHint": "This thread has no messages.",
  "transcript.loadOlder": "Load older messages",
  "transcript.noteLabel": "Internal note",

  // RecordActivityPane — the Activity chatter tab.
  "activity.noRecord": "No record selected",
  "activity.noRecordHint": "Open a record to plan activities.",
  "activity.loading": "Loading activities",
  "activity.disabled": "Activities are not enabled",
  "activity.disabledHint": "This record has no model chatter thread.",
  "activity.emptyTitle": "No activities yet",
  "activity.emptyHint": "Plan the next step for this record.",
  "activity.summary": "Activity summary",
  "activity.notes": "Notes",
  "activity.dueDate": "Due date",
  "activity.noDueDate": "No due date",
  "activity.clearDueDate": "Clear due date",
  "activity.schedule": "Schedule",
  "activity.feedback": "Completion feedback",
  "activity.markDone": "Mark done",
  "activity.cancel": "Cancel activity",
  "activity.stateDone": "Done",
  "activity.stateCanceled": "Canceled",
  "activity.stateOverdue": "Overdue",
  "activity.stateToday": "Today",
  "activity.statePlanned": "Planned",
  "activity.errorSchedule": "Could not schedule activity.",
  "activity.errorComplete": "Could not complete activity.",
  "activity.errorCancel": "Could not cancel activity.",
};

// A translator bound to the `messaging` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useMessagingT = createNamespaceT("messaging", enMessagingMessages);
