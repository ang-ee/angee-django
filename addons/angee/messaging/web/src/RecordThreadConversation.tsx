import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import type { RecordMessagePostKind } from "@angee/gql/console/graphql";
import { holdsPermission } from "@angee/metadata";
import * as React from "react";
import { Avatar, Banner, Button, Checkbox, Chip, EmptyState, ErrorBanner, FieldRoot, Glyph, MessageActions, MessageAttachmentChip, MessageFeed, MessagePartsView, MessageRow, ReactionBar, ReactionPicker, RelativeTime, RemovedDisclosure, RemovedItem, SearchInput, SegmentedControl, Select, Skeleton, SkeletonStatus, Tag, Textarea, avatarInitials, cn, createClientKey, errorMessage, reactionsFromGroups, textRoleVariants, useRuntimeViewAs, useTrashPrompt, useUiT } from "@angee/ui";
import {
  StorageUploadTasks,
  useStorageUpload,
  useStorageT,
  type UploadedFile,
  type UploadTask,
} from "@angee/storage";
import { useDebounce } from "use-debounce";
import { userDisplayName } from "@angee/iam";

import { useMessagingT, type MessagingT } from "./i18n";
import { messagingReactionCopy } from "./reaction-copy";
import { StreamComposer } from "./StreamComposer";
import {
  DeleteRecordMessageDocument,
  MarkRecordMessageDoneDocument,
  MarkRecordThreadReadDocument,
  MessagingRecipientUsersDocument,
  PostRecordMessageDocument,
  READ_MODELS,
  RecordRemovedMessagesDocument,
  RestoreRecordMessageDocument,
  SetRecordMessageReactionDocument,
  SetRecordMessageStarredDocument,
  TrashRecordMessageDocument,
  UpdateRecordMessageDocument,
  type RecordMessageRow,
  type RecordThreadPayload,
  type RecipientUserRow,
  type SuggestedRecipientRow,
} from "./documents";
import { useRecordThreadQuery } from "./useRecordThreadQuery";

const RECIPIENT_MODELS = ["iam.User"] as const;
const SEARCH_DEBOUNCE_MS = 300;
const MESSAGE_PAGE_SIZE = 50;
const QUICK_REACTIONS = ["👍", "❤️", "😂", "🎉"] as const;
const FINISHED_UPLOAD_STATUSES = new Set<UploadTask["status"]>([
  "done",
  "deduped",
  "failed",
]);

interface RecipientOption {
  id: string;
  label: string;
  detail: string;
  follower: boolean;
  suggested: boolean;
  reason: string;
}

interface PostArgs {
  body: string;
  attachmentIds: readonly string[];
  recipientUserIds: readonly string[];
  autofollowRecipients: boolean;
}

/** The controls a `RecordThreadConversation` hands to its optional `header`: the
 *  resolved thread payload, the shared mark-read owner, and a channel to surface a
 *  header-owned failure through the conversation's single error line. */
export interface RecordThreadConversationChrome {
  payload: RecordThreadPayload | undefined;
  markRead: () => Promise<void>;
  markReadPending: boolean;
  reportError: (message: string | null) => void;
  /** The transcript's search, for a header that renders the control itself. */
  search: string;
  setSearch: (value: string) => void;
}

export interface RecordThreadConversationProps {
  modelLabel: string;
  recordId: string;
  /** Chrome rendered above the transcript. The chatter pane supplies its
   *  followers/subtypes/mark-read strip here; a room supplies its own or omits it.
   *  The transcript + composer + mark-read + live-refetch are the same either way. */
  header?: (chrome: RecordThreadConversationChrome) => React.ReactNode;
  /** Where the search control renders: inline above the feed (default), or by the header through `chrome.setSearch`. */
  searchPlacement?: "inline" | "header";
  /** Message types the chatter transcript narrows to (server values: `comment`, `notification`); empty lists all. */
  messageTypes?: readonly string[];
  /** Read the existing window around this public message id and focus its row. */
  around?: string;
  /** Already translated consumer copy; omitted entries use messaging defaults. */
  composerCopy?: { audience?: string; help?: string };
  /** Per-app composer shortcut; Enter sends by default, or Ctrl/Cmd+Enter sends. */
  submitKey?: "enter" | "mod-enter";
  messageLimit?: number;
  /** The stream is the same thread owner with quieter, declaration-driven chrome. */
  stream?: {
    /** Child items inherit their audience from the server-selected child row. */
    audience?: string;
    prompt?: string;
    submitLabel?: string;
    readerLine?: string;
    postKind?: RecordMessagePostKind;
    verbs?: { root: string; reply: string };
    search?: boolean;
    kindSwitch?: boolean;
    recipients?: boolean;
    attachments?: boolean;
    messageTypes?: readonly string[];
    /** Explicit labels for message subtype keys; unlisted kinds have no pill. */
    kindLabels?: Readonly<Record<string, string>>;
  };
}

/** The reusable record-thread conversation: the message transcript + composer over
 *  a record's chatter thread (`record_thread`/`post_record_message`), live-refetched
 *  through `useRecordThreadQuery`. Owns reading, posting, editing,
 *  reactions, stars, mark-done, and mark-read for a `{modelLabel, recordId}`.
 *  The composer requires the record's projected post permission and stays
 *  read-only during view-as preview. The
 *  chatter-specific chrome (follow, notification subtypes, follower counts) is NOT
 *  baked in — it rides the `header` render-prop, so both the record-chatter pane and
 *  a discuss room compose one transcript owner. This is deliberately NOT
 *  `ThreadTranscript`, which reads the `.inbox()`-scoped `messages` collection and
 *  excludes record-attached chatter. */
export function RecordThreadConversation({
  modelLabel,
  recordId,
  header,
  searchPlacement = "inline",
  messageTypes,
  around,
  composerCopy,
  submitKey = "enter",
  messageLimit = MESSAGE_PAGE_SIZE,
  stream,
}: RecordThreadConversationProps): React.ReactElement {
  const t = useMessagingT();
  const preview = useRuntimeViewAs();
  const readOnly = Boolean(preview.viewAs || preview.pending);
  const streamMode = Boolean(stream);
  const enabled = Boolean(modelLabel && recordId);
  const [search, setSearch] = React.useState("");
  const [debouncedSearch] = useDebounce(search.trim(), SEARCH_DEBOUNCE_MS);
  const activeSearch = stream && !stream.search ? "" : debouncedSearch;
  const variables = React.useMemo(
    () => ({
      modelLabel,
      recordId,
      search: activeSearch,
      messageLimit,
      around,
      messageTypes: stream ? [...(stream.messageTypes ?? [])] : [...(messageTypes ?? [])],
    }),
    [modelLabel, recordId, activeSearch, messageLimit, around, stream?.messageTypes, messageTypes, Boolean(stream)],
  );
  const threadQuery = useRecordThreadQuery(variables);
  const threadPayload = threadQuery.data?.record_thread;
  const offeredKinds: readonly RecordMessagePostKind[] = threadPayload ? threadPayload.post_kinds ?? ["COMMENT", "NOTE"] : [];
  const canPost = Boolean(
    enabled && !threadQuery.error && !threadPayload?.error_code &&
    threadPayload?.thread_post_access &&
    offeredKinds.length > 0 &&
    holdsPermission(threadPayload, threadPayload.thread_post_access),
  );
  const recipientVariables = React.useMemo(() => ({ limit: 100 }), []);
  const recipientUsersQuery = useAuthoredQuery(
    MessagingRecipientUsersDocument,
    recipientVariables,
    { enabled: canPost && (stream?.recipients ?? !stream), models: RECIPIENT_MODELS },
  );
  const [postMessage, postState] = useAuthoredMutation(PostRecordMessageDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.post_record_message,
  });
  const [markReadMutation, markReadState] = useAuthoredMutation(MarkRecordThreadReadDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.mark_record_thread_read,
  });
  const [markMessageDone] = useAuthoredMutation(MarkRecordMessageDoneDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.mark_record_message_done,
  });
  const [updateMessage] = useAuthoredMutation(UpdateRecordMessageDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.update_record_message,
  });
  const [deleteMessage] = useAuthoredMutation(DeleteRecordMessageDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.delete_record_message,
  });
  const [trashMessage] = useAuthoredMutation(TrashRecordMessageDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.trash_record_message,
  });
  const [restoreMessage] = useAuthoredMutation(RestoreRecordMessageDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.restore_record_message,
  });
  const [setReaction] = useAuthoredMutation(SetRecordMessageReactionDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_message_reaction,
  });
  const [setStarred] = useAuthoredMutation(SetRecordMessageStarredDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_message_starred,
  });

  const [postKind, setPostKind] = React.useState<RecordMessagePostKind>(stream?.postKind ?? "COMMENT");
  React.useEffect(() => { if (stream?.postKind) setPostKind(stream.postKind); }, [stream?.postKind]);
  const selectedPostKind = offeredKinds.includes(postKind)
    ? postKind
    : offeredKinds[0];
  const [replyToMessage, setReplyToMessage] = React.useState<RecordMessageRow | null>(null);
  const [editingMessageId, setEditingMessageId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const postAttempt = React.useRef<{ intent: string; idempotencyKey: string } | null>(null);

  const recipientOptions = React.useMemo(
    () =>
      recipientOptionsFrom(
        recipientUsersQuery.data?.colleagues ?? [],
        threadPayload?.followers ?? [],
        threadPayload?.suggested_recipients ?? [],
        t,
      ),
    [
      recipientUsersQuery.data?.colleagues,
      threadPayload?.followers,
      threadPayload?.suggested_recipients,
      t,
    ],
  );
  const messageResultCount = threadPayload?.message_result_count ?? 0;
  // Messages arrive server-ordered (chronological ascending); render them verbatim.
  const messages = threadPayload?.messages ?? [];
  const focusedMessageId = messages.find((message) => message.id === around)?.id;
  const conversationRef = React.useCallback((element: HTMLDivElement | null) => {
    if (focusedMessageId) {
      element?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: "center" });
    }
  }, [focusedMessageId]);

  // Drop local reply / editing state the moment its message leaves the feed (a
  // delete elsewhere, a filtered search) so we never edit or reply to a ghost row.
  // An older page is a window, not the feed: browsing it for context keeps the intent.
  React.useEffect(() => {
    if (editingMessageId && !messages.some((message) => message.id === editingMessageId)) {
      setEditingMessageId(null);
    }
    setReplyToMessage((current) =>
      current && !messages.some((message) => message.id === current.id) ? null : current,
    );
  }, [messages, editingMessageId]);

  const handleStartEdit = React.useCallback((messageId: string) => {
    setError(null);
    setEditingMessageId(messageId);
  }, []);
  const handleCancelEdit = React.useCallback(() => setEditingMessageId(null), []);

  const handleSaveEdit = React.useCallback(
    async (messageId: string, body: string): Promise<void> => {
      const next = body.trim();
      if (!next) return;
      setError(null);
      try {
        await updateMessage({ modelLabel, recordId, messageId, body: next });
        setEditingMessageId(null);
      } catch (cause) {
        setError(errorMessage(cause, t("error.update")));
      }
    },
    [modelLabel, recordId, updateMessage, t],
  );

  const handleStartReply = React.useCallback((message: RecordMessageRow) => {
    if (!canPost || readOnly) return;
    setError(null);
    setEditingMessageId(null);
    setReplyToMessage(message);
    if (!streamMode) setPostKind(message.message_type === "NOTIFICATION" ? "NOTE" : "COMMENT");
  }, [canPost, readOnly, streamMode]);

  const handleDeleteMessage = React.useCallback(
    async (message: RecordMessageRow): Promise<void> => {
      setError(null);
      try {
        await deleteMessage({ modelLabel, recordId, messageId: message.id });
      } catch (cause) {
        setError(errorMessage(cause, t("error.delete")));
      }
    },
    [modelLabel, recordId, deleteMessage, t],
  );

  // Moderators move a comment to the trash; it leaves every reader's feed and
  // waits in their "Removed" list. The row's confirmation collected the reason.
  const handleTrashMessage = React.useCallback(
    async (message: RecordMessageRow, reason: string): Promise<void> => {
      setError(null);
      try {
        await trashMessage({ modelLabel, recordId, messageId: message.id, reason });
      } catch (cause) {
        setError(errorMessage(cause, t("error.trash")));
      }
    },
    [modelLabel, recordId, trashMessage, t],
  );

  const handleRestoreMessage = React.useCallback(
    async (messageId: string): Promise<void> => {
      setError(null);
      try {
        await restoreMessage({ modelLabel, recordId, messageId });
      } catch (cause) {
        setError(errorMessage(cause, t("error.restore")));
      }
    },
    [modelLabel, recordId, restoreMessage, t],
  );

  const handleToggleReaction = React.useCallback(
    async (messageId: string, reaction: string): Promise<void> => {
      setError(null);
      try {
        await setReaction({
          modelLabel,
          recordId,
          messageId,
          reaction,
          action: "toggle",
        });
      } catch (cause) {
        setError(errorMessage(cause, t("error.reaction")));
      }
    },
    [modelLabel, recordId, setReaction, t],
  );

  const handleToggleStarred = React.useCallback(
    async (message: RecordMessageRow): Promise<void> => {
      setError(null);
      try {
        await setStarred({
          modelLabel,
          recordId,
          messageId: message.id,
          starred: !message.starred,
        });
      } catch (cause) {
        setError(errorMessage(cause, t("error.starred")));
      }
    },
    [modelLabel, recordId, setStarred, t],
  );

  const handleMarkMessageDone = React.useCallback(
    async (messageId: string): Promise<void> => {
      setError(null);
      try {
        await markMessageDone({ modelLabel, recordId, messageId });
      } catch (cause) {
        setError(errorMessage(cause, t("error.markDone")));
      }
    },
    [modelLabel, recordId, markMessageDone, t],
  );

  const handlePost = React.useCallback(
    async (args: PostArgs): Promise<boolean> => {
      if (!canPost || readOnly || !selectedPostKind) return false;
      setError(null);
      try {
        const input = {
          modelLabel,
          recordId,
          body: args.body,
          kind: selectedPostKind,
          parentMessageId: replyToMessage?.id ?? null,
          attachmentIds: [...args.attachmentIds],
          recipientUserIds: selectedPostKind === "COMMENT" ? [...args.recipientUserIds] : [],
          autofollowRecipients:
            selectedPostKind === "COMMENT" && args.recipientUserIds.length > 0 && args.autofollowRecipients,
        };
        // Retain identity after a failed response; an edited submission starts a new request.
        const intent = JSON.stringify(input);
        if (postAttempt.current?.intent !== intent) {
          postAttempt.current = { intent, idempotencyKey: createClientKey("message") };
        }
        const attempt = postAttempt.current;
        await postMessage({ ...input, idempotencyKey: attempt.idempotencyKey });
        if (postAttempt.current === attempt) postAttempt.current = null;
        setReplyToMessage(null);
        return true;
      } catch (cause) {
        setError(
          errorMessage(
            cause,
            t(selectedPostKind === "NOTE" ? "error.postNote" : "error.postComment"),
          ),
        );
        return false;
      }
    },
    [canPost, readOnly, modelLabel, recordId, selectedPostKind, replyToMessage, postMessage, t],
  );

  const handleMarkRead = React.useCallback(async (): Promise<void> => {
    setError(null);
    try {
      await markReadMutation({ modelLabel, recordId });
    } catch (cause) {
      setError(errorMessage(cause, t("error.markRead")));
    }
  }, [modelLabel, recordId, markReadMutation, t]);

  // Any failure returns a state surface and NO composer — a `record_thread`
  // `NOT_FOUND` (unreadable/nonexistent record) or `BAD_RECORD` (undecodable
  // model/record) must never render a phantom room a non-member could post into.
  const errorCode = threadPayload?.error_code ?? null;

  const chrome: RecordThreadConversationChrome = {
    payload: threadPayload,
    markRead: handleMarkRead,
    markReadPending: markReadState.fetching,
    reportError: setError,
    search,
    setSearch,
  };
  const composer = canPost ? (
    <ChatterComposer
      t={t}
      copy={composerCopy}
      submitKey={submitKey}
      readOnly={readOnly}
      selectedPostKind={selectedPostKind ?? "COMMENT"}
      offeredKinds={offeredKinds}
      stream={stream ? { ...stream, audience: stream.audience ?? threadPayload?.audience_label ?? undefined } : undefined}
      onPostKindChange={setPostKind}
      replyToMessage={replyToMessage}
      onClearReply={() => setReplyToMessage(null)}
      recipientOptions={recipientOptions}
      recipientsLoading={recipientUsersQuery.isFetching}
      posting={postState.fetching}
      onPost={handlePost}
      onError={setError}
    />
  ) : null;
  const errorBanner = <ErrorBanner description={error} dismissLabel={t("error.dismiss")}
    onDismiss={() => setError(null)} />;

  return (
    <div ref={conversationRef} className={cn("flex flex-col gap-4", stream ? "min-h-0" : "min-h-72")}>
      {/* Search and notification controls survive a new query's loading state. */}
      {header?.(chrome)}
      {threadQuery.isFetching && threadQuery.data === undefined ? (
        <SkeletonStatus label={t("chatter.loading")}
          className={cn("flex flex-col gap-4 p-3", stream ? "min-h-40" : "min-h-72")}>
          {!stream ? <Skeleton className="h-9 w-full" /> : null}
          {[0, 1, 2].map((index) => <div key={index} className="flex gap-3">
            <Skeleton className="h-8 w-8 rounded-full" />
            <div className="flex flex-1 flex-col gap-2"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-10 w-3/4" /></div>
          </div>)}
        </SkeletonStatus>
      ) : threadQuery.error || errorCode ? renderThreadError(errorCode, t) : <>
        {(stream ? Boolean(stream.search) : searchPlacement === "inline") ? <SearchInput
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          onClear={() => setSearch("")}
          placeholder={t("chatter.search")}
          aria-label={t("chatter.search")}
        /> : null}
        {messages.length > 0 ? (
          <div className="space-y-3">
            {activeSearch ? (
              <div className={cn(textRoleVariants({ role: "caption" }), "px-1")}>
                {t("chatter.results", { count: messageResultCount })}
              </div>
            ) : null}
            <MessageFeed label={t("chatter.feedLabel")}>
              {messages.map((message) => <MessageFeedRow
                  key={`message:${message.id}`}
                  message={message}
                  focused={message.id === around}
                  editing={editingMessageId === message.id}
                  canReply={canPost}
                  readOnly={readOnly}
                  streamMode={streamMode}
                  streamVerb={stream?.verbs?.[message.is_reply ? "reply" : "root"]}
                  streamKind={stream?.kindLabels?.[message.subtype?.key ?? ""]}
                  audience={stream ? stream.audience ?? threadPayload?.audience_label ?? t("composer.audience") : undefined}
                  t={t}
                  onStartEdit={handleStartEdit}
                  onCancelEdit={handleCancelEdit}
                  onSaveEdit={handleSaveEdit}
                  onStartReply={handleStartReply}
                  onDelete={handleDeleteMessage}
                  onTrash={handleTrashMessage}
                  onToggleReaction={handleToggleReaction}
                  onToggleStarred={handleToggleStarred}
                  onMarkDone={handleMarkMessageDone}
                />)}
            </MessageFeed>
          </div>
        ) : (
          <EmptyState
            icon="comments"
            title={activeSearch ? t("chatter.noMatchTitle") : t(stream ? "stream.empty" : "chatter.emptyTitle")}
            description={
              activeSearch ? t("chatter.noMatchHint") : t(stream ? "stream.emptyHint" : "chatter.emptyHint")
            }
            className="min-h-40 p-4"
          />
        )}
        {threadPayload?.removed_message_count ? (
          <RemovedMessages
            modelLabel={modelLabel}
            recordId={recordId}
            count={threadPayload.removed_message_count}
            readOnly={readOnly}
            t={t}
            onRestore={handleRestoreMessage}
          />
        ) : null}
        {composer}
        {/* The shared danger banner announces via role="alert" and is dismissable —
            `description={null}` renders nothing, so this is the "no error" state too. */}
        {errorBanner}
      </>}
    </div>
  );
}

/** The full `record_thread` failure surface — every arm returns a state fragment
 *  and no composer. The two expected domain codes render an `EmptyState`
 *  (`BAD_RECORD` keeps the chatter-disabled copy; `NOT_FOUND` gets a no-access
 *  surface); the checked default covers a transport failure or any unknown code as
 *  a genuine error through the shared `ErrorBanner` (role="alert"). */
function renderThreadError(errorCode: string | null, t: MessagingT): React.ReactElement {
  switch (errorCode) {
    case "BAD_RECORD":
      return (
        <EmptyState
          icon="comments"
          title={t("chatter.disabled")}
          description={t("chatter.disabledHint")}
          className="min-h-48 p-4"
        />
      );
    case "NOT_FOUND":
      return (
        <EmptyState
          icon="circle-x"
          title={t("chatter.notFoundTitle")}
          description={t("chatter.notFoundHint")}
          className="min-h-48 p-4"
        />
      );
    default:
      return (
        <div className="p-4">
          <ErrorBanner description={t("error.load")} />
        </div>
      );
  }
}

// ---------------------------------------------------------------------------
// Composer — its own child so body keystrokes never re-render the message rows.
// ---------------------------------------------------------------------------

interface ChatterComposerProps {
  t: MessagingT;
  copy: RecordThreadConversationProps["composerCopy"];
  submitKey: NonNullable<RecordThreadConversationProps["submitKey"]>;
  readOnly: boolean;
  selectedPostKind: RecordMessagePostKind;
  offeredKinds: readonly RecordMessagePostKind[];
  stream?: RecordThreadConversationProps["stream"];
  onPostKindChange: (kind: RecordMessagePostKind) => void;
  replyToMessage: RecordMessageRow | null;
  onClearReply: () => void;
  recipientOptions: readonly RecipientOption[];
  recipientsLoading: boolean;
  posting: boolean;
  onPost: (args: PostArgs) => Promise<boolean>;
  onError: (message: string | null) => void;
}

function ChatterComposer({
  t,
  copy,
  submitKey,
  readOnly,
  selectedPostKind,
  offeredKinds,
  stream,
  onPostKindChange,
  replyToMessage,
  onClearReply,
  recipientOptions,
  recipientsLoading,
  posting,
  onPost,
  onError,
}: ChatterComposerProps): React.ReactElement {
  const uiT = useUiT();
  const storageT = useStorageT();
  const disabled = readOnly || posting;
  const [body, setBody] = React.useState("");
  const [selectedRecipientIds, setSelectedRecipientIds] = React.useState<readonly string[]>([]);
  const [autofollowRecipients, setAutofollowRecipients] = React.useState(false);
  const [attachmentDrafts, setAttachmentDrafts] = React.useState<readonly UploadedFile[]>([]);
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const handleUploaded = React.useCallback((files: readonly UploadedFile[]) => {
    if (files.length === 0) return;
    setAttachmentDrafts((current) => appendUploadedFiles(current, files));
  }, []);
  const uploads = useStorageUpload({ onUploaded: handleUploaded });
  const uploadBusy = uploads.tasks.some((task) => !FINISHED_UPLOAD_STATUSES.has(task.status));
  const taskRows = uploads.tasks.filter(isVisibleComposerUploadTask);
  const canSubmit = body.trim() !== "" || ((stream?.attachments ?? !stream) && attachmentDrafts.length > 0);

  function handleKindChange(next: RecordMessagePostKind): void {
    if (disabled) return;
    onPostKindChange(next);
    if (next === "NOTE") {
      setSelectedRecipientIds([]);
      setAutofollowRecipients(false);
    }
  }

  function handleFiles(files: FileList | readonly File[] | null): void {
    if (disabled || !files || files.length === 0) return;
    onError(null);
    uploads.upload(Array.from(files));
  }

  async function submit(): Promise<void> {
    if (disabled || uploadBusy) return;
    const next = body.trim();
    const attachmentIds = (stream?.attachments ?? !stream) ? attachmentDrafts.map((file) => file.id) : [];
    if (!next && attachmentIds.length === 0) return;
    // Clamp to the options actually offered — a recipient may have dropped off the
    // suggestion/follower list between selection and submit.
    const availableIds = new Set(recipientOptions.map((option) => option.id));
    const recipientUserIds = (stream?.recipients ?? !stream)
      ? selectedRecipientIds.filter((id) => availableIds.has(id)) : [];
    const ok = await onPost({
      body: next,
      attachmentIds,
      recipientUserIds,
      autofollowRecipients,
    });
    // Clear only on success. Safe to clear after the await: the textarea is
    // readOnly while `posting`, so `body` cannot have changed mid-flight; on
    // failure the draft is preserved untouched.
    if (!ok) return;
    setBody("");
    setSelectedRecipientIds([]);
    setAutofollowRecipients(false);
    setAttachmentDrafts([]);
  }

  const selectedRecipients = recipientOptions.filter((option) =>
    selectedRecipientIds.includes(option.id),
  );
  const availableRecipients = recipientOptions.filter(
    (option) => !selectedRecipientIds.includes(option.id),
  );

  const showKindSwitch = !stream || stream.kindSwitch;
  const showRecipients = !stream || stream.recipients;
  const showAttachments = !stream || stream.attachments;

  return <StreamComposer value={body} onChange={setBody} onSubmit={() => void submit()}
    disabled={disabled || uploadBusy} ready={canSubmit} submitKey={submitKey}
    prompt={stream?.prompt ?? (selectedPostKind === "NOTE" ? t("composer.logNote") : t("composer.writeComment"))}
    submitLabel={stream?.submitLabel ?? <><Glyph name="send" />
      {selectedPostKind === "NOTE" ? t("composer.log") : t("composer.send")}</>}
    readerLine={stream?.readerLine ?? (stream?.audience ? undefined : copy?.audience ?? t("composer.audience"))}
    audience={stream?.audience}
    before={readOnly ? <Banner tone="warning">{uiT("viewAs.readOnly")}</Banner> : null}
    inputBefore={<>
      {replyToMessage ? <div className="flex items-center gap-2 rounded-6 border border-border-subtle bg-surface px-2 py-1.5">
        <Glyph decorative name="quote" className="shrink-0 text-fg-muted" />
        <div className="min-w-0 flex-1">
          <div className={cn(textRoleVariants({ role: "caption" }), "font-medium")}>
            {t("message.replyingTo", { kind: replyKindLabel(replyToMessage, t) })}
          </div>
          <div className="truncate text-13 text-fg">
            {replyToMessage.preview || editableMessageBody(replyToMessage)}
          </div>
        </div>
        <Button type="button" variant="ghost" size="iconSm" aria-label={t("composer.cancelReply")}
          disabled={disabled} onClick={onClearReply}><Glyph name="x" /></Button>
      </div> : null}
      {showKindSwitch && offeredKinds.length > 1 ? <SegmentedControl<RecordMessagePostKind>
        disabled={disabled} value={selectedPostKind} onValueChange={handleKindChange}
        options={[
          { value: "COMMENT" as const, label: t("composer.comment") },
          { value: "NOTE" as const, label: t("composer.note") },
        ].filter((option) => offeredKinds.includes(option.value))} /> : null}
      {showRecipients && selectedPostKind === "COMMENT" ? <ComposerRecipients
        t={t} disabled={disabled} selected={selectedRecipients} available={availableRecipients}
        loading={recipientsLoading} autofollow={autofollowRecipients}
        onAdd={(id) => setSelectedRecipientIds((current) => current.includes(id) ? current : [...current, id])}
        onRemove={(id) => setSelectedRecipientIds((current) => current.filter((item) => item !== id))}
        onAutofollowChange={setAutofollowRecipients} /> : null}
    </>}
    actions={showAttachments ? <Button type="button" variant="ghost" size="iconSm"
      aria-label={t("composer.attach")} title={t("composer.attach")} disabled={disabled}
      onClick={() => fileInputRef.current?.click()}><Glyph name="attachment" /></Button> : null}
    attachments={showAttachments && attachmentDrafts.length > 0 ? attachmentDrafts.map((file) =>
      <MessageAttachmentChip key={file.id} icon={<Glyph decorative name="attachment" />}
        remove={<Button type="button" variant="ghost" size="iconSm"
          aria-label={t("composer.removeAttachment", { name: file.filename })} disabled={disabled}
          onClick={() => setAttachmentDrafts((current) => current.filter((item) => item.id !== file.id))}>
          <Glyph name="x" />
        </Button>}>{file.filename}</MessageAttachmentChip>) : undefined}
    onFiles={showAttachments ? handleFiles : undefined}
    after={<>
      {showAttachments && taskRows.length > 0 ? <StorageUploadTasks uploads={{ ...uploads, tasks: taskRows }} t={storageT} /> : null}
      {showAttachments ? <input ref={fileInputRef} type="file" disabled={disabled} multiple className="hidden"
        onChange={(event) => { handleFiles(event.target.files); event.target.value = ""; }} /> : null}
      {!stream ? <p className={textRoleVariants({ role: "caption" })}>{copy?.help ?? t("composer.help")}</p> : null}
    </>} />;
}

function ComposerRecipients({
  t,
  disabled,
  selected,
  available,
  loading,
  autofollow,
  onAdd,
  onRemove,
  onAutofollowChange,
}: {
  t: MessagingT;
  disabled: boolean;
  selected: readonly RecipientOption[];
  available: readonly RecipientOption[];
  loading: boolean;
  autofollow: boolean;
  onAdd: (id: string) => void;
  onRemove: (id: string) => void;
  onAutofollowChange: (checked: boolean) => void;
}): React.ReactElement {
  const placeholder = loading
    ? t("composer.loadingRecipients")
    : available.length > 0
      ? t("composer.addRecipient")
      : t("composer.noRecipients");
  return (
    <div className="space-y-1.5 rounded-6 border border-border-subtle bg-surface px-2 py-2">
      <div className="flex items-center gap-2">
        <Glyph decorative name="users" className="shrink-0 text-fg-muted" />
        <Select
          value=""
          disabled={disabled || loading || available.length === 0}
          placeholder={placeholder}
          aria-label={t("composer.addRecipient")}
          className="min-w-0 flex-1"
          options={available.map((option) => ({
            value: option.id,
            label: recipientOptionLabel(option, t),
          }))}
          onValueChange={(value) => {
            if (value) onAdd(value);
          }}
        />
        {selected.length > 0 ? (
          <FieldRoot className="inline-flex h-8 items-center gap-1.5 rounded-6 border border-border-subtle px-2 text-12 text-fg-muted">
            <FieldRoot.Item>
            <Checkbox
              disabled={disabled}
              size="sm"
              checked={autofollow}
              onCheckedChange={(next) => onAutofollowChange(next)}
            />
              <FieldRoot.Label className="text-12 text-fg-muted">
                {t("composer.followRecipients")}
              </FieldRoot.Label>
            </FieldRoot.Item>
          </FieldRoot>
        ) : null}
      </div>
      {selected.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {selected.map((option) => (
            <Chip key={option.id} tone="neutral" size="md" className="gap-1.5">
              <span className="min-w-0 max-w-40 truncate">{option.label}</span>
              {option.follower ? (
                <Tag tone="neutral" density="micro">
                  {t("composer.follower")}
                </Tag>
              ) : null}
              {option.suggested ? (
                <Tag tone="info" density="micro">
                  {option.reason || t("composer.suggested")}
                </Tag>
              ) : null}
              <button
                type="button"
                aria-label={t("composer.removeRecipient", { name: option.label })}
                disabled={disabled}
                onClick={() => onRemove(option.id)}
                className="shrink-0 text-fg-muted outline-none hover:text-fg focus-visible:focus-ring"
              >
                <Glyph name="x" className="size-3" />
              </button>
            </Chip>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feed row — a memoized `MessageRow` composing the shared feed atoms. Editing
// draft state lives inside the editor so keystrokes never touch siblings.
// ---------------------------------------------------------------------------

interface MessageFeedRowProps {
  message: RecordMessageRow;
  focused: boolean;
  editing: boolean;
  canReply: boolean;
  readOnly: boolean;
  t: MessagingT;
  streamMode: boolean;
  streamVerb?: string;
  streamKind?: string;
  audience?: string;
  onStartEdit: (messageId: string) => void;
  onCancelEdit: () => void;
  onSaveEdit: (messageId: string, body: string) => void;
  onStartReply: (message: RecordMessageRow) => void;
  onDelete: (message: RecordMessageRow) => void;
  onTrash: (message: RecordMessageRow, reason: string) => void;
  onToggleReaction: (messageId: string, reaction: string) => void;
  onToggleStarred: (message: RecordMessageRow) => void;
  onMarkDone: (messageId: string) => void;
}

const MessageFeedRow = React.memo(function MessageFeedRow({
  message,
  focused,
  editing,
  canReply,
  readOnly,
  t,
  streamMode,
  streamVerb,
  streamKind,
  audience,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onStartReply,
  onDelete,
  onTrash,
  onToggleReaction,
  onToggleStarred,
  onMarkDone,
}: MessageFeedRowProps): React.ReactElement {
  const editableBody = editableMessageBody(message);
  // The server resolves actor-readable identity; older payloads retain their
  // actor-scoped sender projection as a fallback.
  const author = message.is_self ? t("message.you") :
    message.author_label || message.sender?.display_name || message.sender?.value || t("message.author");
  const timestamp = message.sent_at ?? message.created_at;
  const subtypeDescription = message.subtype?.description || message.subtype?.name || "";
  const directionTag = directionLabel(message.direction, t);
  const edited = Boolean(message.edited_at || message.status === "EDITED");
  const reactions = reactionsFromGroups(
    message.reaction_groups,
    messagingReactionCopy(t),
  );
  const activeReactions = message.reaction_groups
    .filter((group) => group.self_reacted)
    .map((group) => group.reaction);

  if (editing) {
    return (
      <MessageRow
        id={`message-${message.id}`}
        aria-current={focused || undefined}
        avatar={<Avatar size="sm" initials={avatarInitials(author)} alt={author} />}
        author={author}
        className={streamMode && message.is_reply ? "ml-3 border-l-2 border-border-subtle pl-3" : undefined}
      >
        <MessageEditor
          initialBody={editableBody}
          t={t}
          onCancel={onCancelEdit}
          onSave={(body) => onSaveEdit(message.id, body)}
        />
      </MessageRow>
    );
  }

  return (
    <MessageRow
      id={`message-${message.id}`}
      aria-current={focused || undefined}
      avatar={<Avatar size="sm" initials={avatarInitials(author)} alt={author} />}
      author={author}
      timestamp={timestamp}
      className={streamMode && message.is_reply ? "ml-3 border-l-2 border-border-subtle pl-3" : undefined}
      channel={streamMode ? <>
        {audience ? <Chip tone="neutral" size="md">{audience}</Chip> : null}
        {streamKind ? <Tag tone="info" density="micro">{streamKind}</Tag> : null}
      </> : directionTag ? <Tag tone="info" density="micro">{directionTag}</Tag> : undefined}
      meta={streamVerb || edited ? <>
        {streamVerb ? <span>{streamVerb}</span> : null}
        {edited ? <>{streamVerb ? " · " : null}{t("chatter.editedMeta")}
          {message.edited_at ? <> <RelativeTime value={message.edited_at} /></> : null}</> : null}
      </> : undefined}
      reactions={
        reactions.length > 0 ? (
          <ReactionBar
            reactions={reactions}
            label={t("message.reactions")}
            onToggle={(reaction) => onToggleReaction(message.id, reaction)}
          />
        ) : undefined
      }
      actions={
        <MessageActions>
          {message.needaction ? (
            <Button
              type="button"
              variant="ghost"
              size="iconSm"
              aria-label={t("message.markDone")}
              className="text-brand"
              onClick={() => onMarkDone(message.id)}
            >
              <Glyph name="check" />
            </Button>
          ) : null}
          {canReply ? (
            <Button
              type="button"
              variant="ghost"
              size="iconSm"
              aria-label={t("message.reply")}
              disabled={readOnly}
              onClick={() => onStartReply(message)}
            >
              <Glyph name="quote" />
            </Button>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            aria-label={message.starred ? t("message.unstar") : t("message.star")}
            aria-pressed={message.starred}
            className={message.starred ? "text-warning-text" : undefined}
            onClick={() => onToggleStarred(message)}
          >
            <Glyph name="star" />
          </Button>
          <ReactionPicker
            options={QUICK_REACTIONS}
            active={activeReactions}
            label={t("message.addReaction")}
            onToggle={(reaction) => onToggleReaction(message.id, reaction)}
          />
          {message.can_edit && !readOnly ? (
            <Button
              type="button"
              variant="ghost"
              size="iconSm"
              aria-label={t("message.edit")}
              onClick={() => onStartEdit(message.id)}
            >
              <Glyph name="pencil" />
            </Button>
          ) : null}
          {message.can_delete ? (
            <Button
              type="button"
              variant="ghost"
              size="iconSm"
              aria-label={t("message.delete")}
              onClick={() => onDelete(message)}
            >
              <Glyph name="trash" />
            </Button>
          ) : null}
          {message.can_trash && !readOnly ? (
            <MessageTrashButton t={t} onConfirmed={(reason) => onTrash(message, reason)} />
          ) : null}
        </MessageActions>
      }
    >
      {message.parent ? (
        <div className="mb-2 rounded-6 border-l-2 border-border-subtle bg-surface px-2 py-1">
          <div className={cn(textRoleVariants({ role: "caption" }), "font-medium")}>
            {t("message.replyingTo", { kind: replyKindLabel(message.parent, t) })}
          </div>
          <div className="truncate text-13 text-fg-muted">{message.parent.preview}</div>
        </div>
      ) : null}
      {subtypeDescription && message.message_type !== "COMMENT" && !streamMode ? (
        <div className={cn(textRoleVariants({ role: "caption" }), "mb-1 font-medium")}>
          {subtypeDescription}
        </div>
      ) : null}
      <MessagePartsView parts={message.parts} />
    </MessageRow>
  );
});

/** A moderator's remove control: the shared trash confirmation collects an optional reason. */
function MessageTrashButton({ t, onConfirmed }: {
  t: MessagingT;
  onConfirmed: (reason: string) => void;
}): React.ReactElement {
  const askTrash = useTrashPrompt();
  return (
    <Button
      type="button"
      variant="ghost"
      size="iconSm"
      aria-label={t("message.remove")}
      onClick={() => {
        void askTrash().then((answer) => {
          if (answer) onConfirmed(answer.reason);
        });
      }}
    >
      <Glyph name="archive" fallbackName="trash" />
    </Button>
  );
}

/**
 * The moderators' "Removed (n)" list: the server reports a count only to the
 * record's moderators, so readers never mount this or learn that it exists.
 */
function RemovedMessages({ modelLabel, recordId, count, readOnly, t, onRestore }: {
  modelLabel: string;
  recordId: string;
  count: number;
  readOnly: boolean;
  t: MessagingT;
  onRestore: (messageId: string) => void;
}): React.ReactElement | null {
  const variables = React.useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  const removedQuery = useAuthoredQuery(RecordRemovedMessagesDocument, variables, { models: READ_MODELS });
  const payload = removedQuery.data?.record_removed_messages;
  if (removedQuery.error || payload?.error_code) {
    return <ErrorBanner description={t("error.removed")} />;
  }
  return (
    <RemovedDisclosure count={payload?.message_result_count ?? count}>
      {(payload?.messages ?? []).map((message) => (
        <RemovedItem
          key={message.id}
          label={message.author_label || t("message.author")}
          trashedByLabel={message.trashed_by_label}
          trashReason={message.trash_reason}
          {...(readOnly ? {} : { onRestore: () => onRestore(message.id) })}
        >
          <span className="line-clamp-2 text-13 text-fg-muted">{message.preview}</span>
        </RemovedItem>
      ))}
    </RemovedDisclosure>
  );
}

function MessageEditor({
  initialBody,
  t,
  onCancel,
  onSave,
}: {
  initialBody: string;
  t: MessagingT;
  onCancel: () => void;
  onSave: (body: string) => void;
}): React.ReactElement {
  const [body, setBody] = React.useState(initialBody);
  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(body);
      }}
    >
      <Textarea
        value={body}
        onChange={(event) => setBody(event.currentTarget.value)}
        rows={3}
        resize="none"
        aria-label={t("message.editLabel")}
      />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          <Glyph name="x" />
          {t("message.cancel")}
        </Button>
        <Button type="submit" variant="primary" size="sm" disabled={body.trim() === ""}>
          <Glyph name="check" />
          {t("message.save")}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

function editableMessageBody(message: Pick<RecordMessageRow, "parts" | "preview">): string {
  const part = message.parts.find(
    (item) => normaliseMessagePartValue(item.role) === "BODY" && item.fragment?.text,
  );
  return part?.fragment?.text ?? message.preview ?? "";
}

function normaliseMessagePartValue(value: string | null | undefined): string {
  return (value ?? "").trim().toUpperCase();
}

function replyKindLabel(message: { message_type?: string | null }, t: MessagingT): string {
  if (message.message_type === "NOTIFICATION") return t("message.kindNote");
  return t("message.kindMessage");
}

function directionLabel(direction: string | null | undefined, t: MessagingT): string | null {
  // Read the SDL's UPPERCASE `Direction` enum verbatim, the same convention as the
  // `message_type` reads below — one enum-casing convention across the file.
  if (direction === "INBOUND") return t("message.directionInbound");
  if (direction === "OUTBOUND") return t("message.directionOutbound");
  return null;
}

function recipientOptionsFrom(
  users: readonly RecipientUserRow[],
  followers: ReadonlyArray<NonNullable<RecordThreadPayload["followers"]>[number]>,
  suggestions: readonly SuggestedRecipientRow[],
  t: MessagingT,
): readonly RecipientOption[] {
  const userFallback = t("composer.userFallback");
  const byId = new Map<string, RecipientOption>();
  for (const user of users) {
    if (user.is_active === false) continue;
    byId.set(user.id, {
      id: user.id,
      label: userDisplayName(user, userFallback),
      detail: user.email || user.username || "",
      follower: false,
      suggested: false,
      reason: "",
    });
  }
  for (const suggestion of suggestions) {
    const user = suggestion.user;
    if (user.is_active === false) continue;
    const previous = byId.get(user.id);
    byId.set(user.id, {
      id: user.id,
      label: userDisplayName(user, userFallback),
      detail: previous?.detail || user.email || user.username || "",
      follower: previous?.follower ?? false,
      suggested: true,
      reason: suggestion.reason || t("composer.suggested"),
    });
  }
  for (const follower of followers) {
    const party = follower.party;
    const user = follower.user;
    if (!party || !user || user.is_active === false) continue;
    const previous = byId.get(user.id);
    byId.set(user.id, {
      id: user.id,
      label: previous?.label ?? (party.display_name || userFallback),
      detail: previous?.detail || user.username || "",
      follower: true,
      suggested: previous?.suggested ?? false,
      reason: previous?.reason ?? "",
    });
  }
  return [...byId.values()].sort(
    (left, right) =>
      Number(right.suggested) - Number(left.suggested) ||
      left.label.localeCompare(right.label) ||
      left.id.localeCompare(right.id),
  );
}

function recipientOptionLabel(option: RecipientOption, t: MessagingT): string {
  const label = option.suggested
    ? `${option.label} · ${option.reason || t("composer.suggested")}`
    : option.label;
  return option.detail ? `${label} · ${option.detail}` : label;
}

function appendUploadedFiles(
  current: readonly UploadedFile[],
  uploaded: readonly UploadedFile[],
): readonly UploadedFile[] {
  const seen = new Set(current.map((file) => file.id));
  const next = [...current];
  for (const file of uploaded) {
    if (seen.has(file.id)) continue;
    seen.add(file.id);
    next.push(file);
  }
  return next;
}

function isVisibleComposerUploadTask(task: UploadTask): boolean {
  return task.status !== "done" && task.status !== "deduped";
}
