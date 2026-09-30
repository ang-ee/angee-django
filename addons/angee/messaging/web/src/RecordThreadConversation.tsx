import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { holdsPermission } from "@angee/metadata";
import * as React from "react";
import { Avatar, Banner, Button, Checkbox, Chip, EmptyState, ErrorBanner, FieldRoot, Glyph, MessageActions, MessageAttachmentChip, MessageFeed, MessagePartsView, MessageRow, ReactionBar, ReactionPicker, RelativeTime, SearchInput, SegmentedControl, Select, Skeleton, SkeletonStatus, Tag, Textarea, avatarInitials, cn, createClientKey, dateFromValue, errorMessage, formatDate, formatDateStorage, reactionsFromGroups, textRoleVariants, useRuntimeViewAs, useUiT } from "@angee/ui";
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
  RecordThreadDocument,
  SetRecordMessageReactionDocument,
  SetRecordMessageStarredDocument,
  UpdateRecordMessageDocument,
  type RecordMessageRow,
  type RecordActivityRow,
  type RecordThreadPayload,
  type RecipientUserRow,
  type SuggestedRecipientRow,
} from "./documents";

const RECIPIENT_MODELS = ["iam.User"] as const;
const SEARCH_DEBOUNCE_MS = 300;
const QUICK_REACTIONS = ["👍", "❤️", "😂", "🎉"] as const;
const FINISHED_UPLOAD_STATUSES = new Set<UploadTask["status"]>([
  "done",
  "deduped",
  "failed",
]);

type ChatterPostKind = "comment" | "note";

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
}

export interface RecordThreadConversationProps {
  modelLabel: string;
  recordId: string;
  /** Chrome rendered above the transcript. The chatter pane supplies its
   *  followers/subtypes/mark-read strip here; a room supplies its own or omits it.
   *  The transcript + composer + mark-read + live-refetch are the same either way. */
  header?: (chrome: RecordThreadConversationChrome) => React.ReactNode;
  /** Override the label used when an exchange was recorded on a later day. */
  activityCopy?: { recordedOn: (day: string) => string };
  /** Already translated consumer copy; omitted entries use messaging defaults. */
  composerCopy?: { audience?: string; help?: string };
  /** Per-app composer shortcut; Enter sends by default, or Ctrl/Cmd+Enter sends. */
  submitKey?: "enter" | "mod-enter";
  /** The stream is the same thread owner with quieter, declaration-driven chrome. */
  stream?: {
    /** Child items inherit their audience from the server-selected child row. */
    audience?: string;
    prompt?: string;
    submitLabel?: string;
    readerLine?: string;
    postKind?: ChatterPostKind;
    verbs?: { root: string; reply: string };
    search?: boolean;
    kindSwitch?: boolean;
    recipients?: boolean;
    attachments?: boolean;
    activities?: boolean;
    messageTypes?: readonly string[];
    /** Explicit labels for message subtype keys; unlisted kinds have no pill. */
    kindLabels?: Readonly<Record<string, string>>;
  };
}

/** The reusable record-thread conversation: the message transcript + composer over
 *  a record's chatter thread (`record_thread`/`post_record_message`), live-refetched
 *  through the `READ_MODELS` invalidation set. Owns reading, posting, editing,
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
  activityCopy,
  composerCopy,
  submitKey = "enter",
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
      messageLimit: 50,
      messageTypes: stream ? [...(stream.messageTypes ?? [])] : [],
    }),
    [modelLabel, recordId, activeSearch, stream?.messageTypes, Boolean(stream)],
  );
  const threadQuery = useAuthoredQuery(RecordThreadDocument, variables, {
    enabled,
    models: READ_MODELS,
  });
  const threadPayload = threadQuery.data?.record_thread;
  const offeredKinds = (threadPayload ? threadPayload.post_kinds ?? ["COMMENT", "NOTE"] : [])
    .map((kind) => kind.toLowerCase());
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
  const [setReaction] = useAuthoredMutation(SetRecordMessageReactionDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_message_reaction,
  });
  const [setStarred] = useAuthoredMutation(SetRecordMessageStarredDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_message_starred,
  });

  const [postKind, setPostKind] = React.useState<ChatterPostKind>(stream?.postKind ?? "comment");
  React.useEffect(() => { if (stream?.postKind) setPostKind(stream.postKind); }, [stream?.postKind]);
  const selectedPostKind = offeredKinds.includes(postKind)
    ? postKind
    : offeredKinds[0] as ChatterPostKind | undefined;
  const [replyToMessage, setReplyToMessage] = React.useState<RecordMessageRow | null>(null);
  const [editingMessageId, setEditingMessageId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const postAttempt = React.useRef<{ intent: string; clientCreationKey: string } | null>(null);

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
  const transcript = transcriptEntries(messages, (!stream || stream.activities) ? threadPayload?.activities ?? [] : []);
  const activityTypes = new Map((threadPayload?.activity_types ?? []).map((type) => [type.key, type]));

  // Drop local reply / editing state the moment its message leaves the feed (a
  // delete elsewhere, a filtered search) so we never edit or reply to a ghost row.
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
    if (!streamMode) setPostKind(message.message_type === "NOTIFICATION" ? "note" : "comment");
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
      if (!canPost || readOnly) return false;
      setError(null);
      try {
        const input = {
          modelLabel,
          recordId,
          body: args.body,
          kind: selectedPostKind ?? "comment",
          parentMessageId: replyToMessage?.id ?? null,
          attachmentIds: [...args.attachmentIds],
          recipientUserIds: selectedPostKind === "comment" ? [...args.recipientUserIds] : [],
          autofollowRecipients:
            selectedPostKind === "comment" && args.recipientUserIds.length > 0 && args.autofollowRecipients,
        };
        // Retain identity after a failed response; an edited submission starts a new request.
        const intent = JSON.stringify(input);
        if (postAttempt.current?.intent !== intent) {
          postAttempt.current = { intent, clientCreationKey: createClientKey("message") };
        }
        const attempt = postAttempt.current;
        await postMessage({ ...input, clientCreationKey: attempt.clientCreationKey });
        if (postAttempt.current === attempt) postAttempt.current = null;
        setReplyToMessage(null);
        return true;
      } catch (cause) {
        setError(
          errorMessage(
            cause,
            t(selectedPostKind === "note" ? "error.postNote" : "error.postComment"),
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

  if (threadQuery.isFetching && threadQuery.data === undefined) {
    return <SkeletonStatus label={t("chatter.loading")}
      className={cn("flex flex-col gap-4 p-3", stream ? "min-h-40" : "min-h-72")}>
      {!stream ? <Skeleton className="h-9 w-full" /> : null}
      {[0, 1, 2].map((index) => <div key={index} className="flex gap-3">
        <Skeleton className="h-8 w-8 rounded-full" />
        <div className="flex flex-1 flex-col gap-2"><Skeleton className="h-4 w-1/3" /><Skeleton className="h-10 w-3/4" /></div>
      </div>)}
    </SkeletonStatus>;
  }
  // Any failure returns a state surface and NO composer — a `record_thread`
  // `NOT_FOUND` (unreadable/nonexistent record) or `BAD_RECORD` (undecodable
  // model/record) must never render a phantom room a non-member could post into.
  const errorCode = threadPayload?.error_code ?? null;
  if (threadQuery.error || errorCode) {
    return renderThreadError(errorCode, t);
  }

  const chrome: RecordThreadConversationChrome = {
    payload: threadPayload,
    markRead: handleMarkRead,
    markReadPending: markReadState.fetching,
    reportError: setError,
  };

  return (
    <div className={cn("flex flex-col gap-4", stream ? "min-h-0" : "min-h-72")}>
      {header?.(chrome)}
      {(stream?.search ?? !stream) ? <SearchInput
        value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
        onClear={() => setSearch("")}
        placeholder={t("chatter.search")}
        aria-label={t("chatter.search")}
      /> : null}
      {transcript.length > 0 ? (
        <div className="space-y-3">
          {activeSearch ? (
            <div className={cn(textRoleVariants({ role: "caption" }), "px-1")}>
              {t("chatter.results", { count: messageResultCount })}
            </div>
          ) : null}
          <MessageFeed label={t("chatter.feedLabel")}>
            {transcript.map((entry) => entry.kind === "activity" ? (
              <CompletedActivityRow
                key={`activity:${entry.activity.id}`}
                activity={entry.activity}
                type={activityTypes.get(entry.activity.activity_type)}
                recordedOn={activityCopy?.recordedOn ?? ((day) => t("activity.recordedOn", { day }))}
              />
            ) : (
              <MessageFeedRow
                key={`message:${entry.message.id}`}
                message={entry.message}
                editing={editingMessageId === entry.message.id}
                canReply={canPost}
                readOnly={readOnly}
                streamMode={streamMode}
                streamVerb={stream?.verbs?.[entry.message.is_reply ? "reply" : "root"]}
                streamKind={stream?.kindLabels?.[entry.message.subtype?.key ?? ""]}
                audience={stream ? stream.audience ?? threadPayload?.audience_label ?? t("composer.audience") : undefined}
                t={t}
                onStartEdit={handleStartEdit}
                onCancelEdit={handleCancelEdit}
                onSaveEdit={handleSaveEdit}
                onStartReply={handleStartReply}
                onDelete={handleDeleteMessage}
                onToggleReaction={handleToggleReaction}
                onToggleStarred={handleToggleStarred}
                onMarkDone={handleMarkMessageDone}
              />
            ))}
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
      {canPost ? (
        <ChatterComposer
          t={t}
          copy={composerCopy}
          submitKey={submitKey}
          readOnly={readOnly}
          selectedPostKind={selectedPostKind ?? "comment"}
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
      ) : null}
      {/* The shared danger banner announces via role="alert" and is dismissable —
          `description={null}` renders nothing, so this is the "no error" state too. */}
      <ErrorBanner
        description={error}
        dismissLabel={t("error.dismiss")}
        onDismiss={() => setError(null)}
      />
    </div>
  );
}

type TranscriptEntry =
  | { kind: "message"; message: RecordMessageRow; day: string; at: string }
  | { kind: "activity"; activity: RecordActivityRow; day: string; at: string };

function transcriptEntries(
  messages: readonly RecordMessageRow[],
  activities: readonly RecordActivityRow[],
): TranscriptEntry[] {
  const messageEntries: TranscriptEntry[] = messages.map((message) => {
    const at = message.sent_at ?? message.created_at;
    return { kind: "message", message, at, day: formatDateStorage(dateFromValue(at)) ?? "" };
  });
  const activityEntries: TranscriptEntry[] = activities
    .filter((activity) => activity.status === "DONE")
    .map((activity) => ({
      kind: "activity", activity, at: activity.completed_at ?? "",
      day: activity.due_date ?? formatDateStorage(dateFromValue(activity.completed_at)) ?? "",
    }));
  const entries: TranscriptEntry[] = [];
  let nextActivity = 0;
  for (const messageEntry of messageEntries) {
    while (nextActivity < activityEntries.length) {
      const activityEntry = activityEntries[nextActivity]!;
      const dayOrder = activityEntry.day.localeCompare(messageEntry.day);
      if (dayOrder > 0 || (dayOrder === 0 &&
        Date.parse(activityEntry.at) > Date.parse(messageEntry.at))) break;
      entries.push(activityEntry);
      nextActivity += 1;
    }
    entries.push(messageEntry);
  }
  entries.push(...activityEntries.slice(nextActivity));
  return entries;
}

function CompletedActivityRow({ activity, type, recordedOn }: {
  activity: RecordActivityRow;
  type: { name: string; glyph: string } | undefined;
  recordedOn: (day: string) => string;
}): React.ReactElement {
  const recordedDay = formatDateStorage(dateFromValue(activity.completed_at));
  const occurredDay = activity.due_date ?? recordedDay;
  return (
    <MessageRow
      avatar={<Glyph decorative name={type?.glyph || "activity"} fallbackName="activity" />}
      author={activity.created_by ? userDisplayName(activity.created_by, "") : undefined}
      channel={<span className="text-13 font-medium">{type?.name ?? activity.activity_type}</span>}
      meta={<>
        {occurredDay ? <time dateTime={occurredDay}>{formatDate(occurredDay)}</time> : null}
        {recordedDay && recordedDay !== occurredDay ? <> · {recordedOn(formatDate(recordedDay))}</> : null}
      </>}
    >
      {activity.note || activity.summary}
    </MessageRow>
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
  selectedPostKind: ChatterPostKind;
  offeredKinds: readonly string[];
  stream?: RecordThreadConversationProps["stream"];
  onPostKindChange: (kind: ChatterPostKind) => void;
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

  function handleKindChange(next: ChatterPostKind): void {
    if (disabled) return;
    onPostKindChange(next);
    if (next === "note") {
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
    prompt={stream?.prompt ?? (selectedPostKind === "note" ? t("composer.logNote") : t("composer.writeComment"))}
    submitLabel={stream?.submitLabel ?? <><Glyph name="send" />
      {selectedPostKind === "note" ? t("composer.log") : t("composer.send")}</>}
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
      {showKindSwitch && offeredKinds.length > 1 ? <SegmentedControl<ChatterPostKind>
        disabled={disabled} value={selectedPostKind} onValueChange={handleKindChange}
        options={[
          { value: "comment" as const, label: t("composer.comment") },
          { value: "note" as const, label: t("composer.note") },
        ].filter((option) => offeredKinds.includes(option.value))} /> : null}
      {showRecipients && selectedPostKind === "comment" ? <ComposerRecipients
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
  onToggleReaction: (messageId: string, reaction: string) => void;
  onToggleStarred: (message: RecordMessageRow) => void;
  onMarkDone: (messageId: string) => void;
}

const MessageFeedRow = React.memo(function MessageFeedRow({
  message,
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
  onToggleReaction,
  onToggleStarred,
  onMarkDone,
}: MessageFeedRowProps): React.ReactElement {
  const editableBody = editableMessageBody(message);
  // The server resolves actor-readable identity; older payloads retain their
  // actor-scoped sender projection as a fallback.
  const author = message.is_self ? t("message.you") :
    message.author_label || message.sender?.display_name || message.sender?.value || t("message.author");
  const trackingValues = [...message.tracking_values].sort(
    (left, right) =>
      left.position - right.position || left.field_label.localeCompare(right.field_label),
  );
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
      tracking={
        trackingValues.length > 0 ? (
          <dl className="space-y-1 rounded-6 bg-surface-inset p-2">
            {trackingValues.map((tracking) => (
              <div
                key={tracking.id}
                className="grid grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-2 text-13"
              >
                <dt className="truncate font-medium text-fg-muted">{tracking.field_label}</dt>
                <dd className="min-w-0 text-fg">
                  <span className="text-fg-muted">{tracking.old_display || "—"}</span>
                  <span aria-hidden="true"> → </span>
                  <span>{tracking.new_display || "—"}</span>
                </dd>
              </div>
            ))}
          </dl>
        ) : undefined
      }
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
  if (message.message_type === "AUTO_COMMENT") return t("message.kindUpdate");
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
