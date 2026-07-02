import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Avatar, Button, Checkbox, Chip, EmptyState, FieldRoot, Glyph, LoadingPanel, MessageActions, MessageAttachmentChip, MessageComposer, MessageComposerHint, MessageFeed, MessageRow, ReactionBar, ReactionPicker, SearchInput, SegmentedControl, Select, Tag, Textarea, UploadDropTarget, avatarInitials, cn, errorMessage, messageComposerInputClassName, textRoleVariants, type Reaction } from "@angee/ui";
import { formatSize } from "@angee/ui/preview/index";
import type { ChatterViewContext } from "@angee/ui/runtime";
import {
  useStorageUpload,
  type UploadedFile,
  type UploadTask,
} from "@angee/storage";
import { useDebounce } from "use-debounce";

import { useMessagingT } from "./i18n";
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
  SetRecordFollowingDocument,
  UpdateRecordMessageDocument,
  type RecordMessageRow,
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
type MessagingT = ReturnType<typeof useMessagingT>;

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

export interface RecordChatterPaneProps {
  context: ChatterViewContext;
}

export function RecordChatterPane({ context }: RecordChatterPaneProps): React.ReactElement {
  const t = useMessagingT();
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  const enabled = Boolean(modelLabel && recordId);
  const [search, setSearch] = React.useState("");
  const [debouncedSearch] = useDebounce(search.trim(), SEARCH_DEBOUNCE_MS);
  const variables = React.useMemo(
    () => ({
      modelLabel: modelLabel ?? "",
      recordId: recordId ?? "",
      search: debouncedSearch,
      messageLimit: 50,
    }),
    [modelLabel, recordId, debouncedSearch],
  );
  const threadQuery = useAuthoredQuery(RecordThreadDocument, variables, {
    enabled,
    models: READ_MODELS,
  });
  const recipientVariables = React.useMemo(() => ({ limit: 100, offset: 0 }), []);
  const recipientUsersQuery = useAuthoredQuery(
    MessagingRecipientUsersDocument,
    recipientVariables,
    { enabled, models: RECIPIENT_MODELS },
  );
  const [postMessage, postState] = useAuthoredMutation(PostRecordMessageDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.post_record_message,
  });
  const [setFollowing, followState] = useAuthoredMutation(SetRecordFollowingDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_following,
  });
  const [markRead, markReadState] = useAuthoredMutation(MarkRecordThreadReadDocument, {
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

  const [postKind, setPostKind] = React.useState<ChatterPostKind>("comment");
  const [replyToMessage, setReplyToMessage] = React.useState<RecordMessageRow | null>(null);
  const [editingMessageId, setEditingMessageId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const threadPayload = threadQuery.data?.record_thread;
  const recipientOptions = React.useMemo(
    () =>
      recipientOptionsFrom(
        recipientUsersQuery.data?.users ?? [],
        threadPayload?.followers ?? [],
        threadPayload?.suggested_recipients ?? [],
      ),
    [
      recipientUsersQuery.data?.users,
      threadPayload?.followers,
      threadPayload?.suggested_recipients,
    ],
  );
  const followerCount = threadPayload?.follower_count ?? 0;
  const attachmentCount = threadPayload?.attachment_count ?? 0;
  const unreadCount = threadPayload?.unread_count ?? 0;
  const deliveryErrorCount = threadPayload?.message_has_error_counter ?? 0;
  const isFollowing = Boolean(threadPayload?.is_following);
  const selfFollower = threadPayload?.self_follower ?? null;
  const subtypeOptions = React.useMemo(
    () => [...(threadPayload?.subtypes ?? [])].filter((subtype) => !subtype.internal),
    [threadPayload?.subtypes],
  );
  const selectedSubtypeKeys = React.useMemo(() => {
    const explicit = subtypeKeysFromValue(selfFollower?.subtype_keys);
    if (explicit.length > 0) return explicit.map(String);
    return subtypeOptions.filter((subtype) => subtype.default).map((subtype) => subtype.key);
  }, [selfFollower?.subtype_keys, subtypeOptions]);
  const messageResultCount = threadPayload?.message_result_count ?? 0;
  // Messages arrive server-ordered (chronological ascending); render them verbatim.
  const messages = threadPayload?.messages ?? [];

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
      if (!next || !modelLabel || !recordId) return;
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
    setError(null);
    setEditingMessageId(null);
    setReplyToMessage(message);
    setPostKind(message.message_type === "NOTIFICATION" ? "note" : "comment");
  }, []);

  const handleDeleteMessage = React.useCallback(
    async (message: RecordMessageRow): Promise<void> => {
      if (!modelLabel || !recordId) return;
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
      if (!modelLabel || !recordId) return;
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
      if (!modelLabel || !recordId) return;
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
      if (!modelLabel || !recordId) return;
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
      if (!modelLabel || !recordId) return false;
      setError(null);
      try {
        await postMessage({
          modelLabel,
          recordId,
          body: args.body,
          kind: postKind,
          parentMessageId: replyToMessage?.id ?? null,
          attachmentIds: [...args.attachmentIds],
          recipientUserIds: postKind === "comment" ? [...args.recipientUserIds] : [],
          autofollowRecipients:
            postKind === "comment" && args.recipientUserIds.length > 0 && args.autofollowRecipients,
        });
        setReplyToMessage(null);
        return true;
      } catch (cause) {
        setError(
          errorMessage(
            cause,
            t(postKind === "note" ? "error.postNote" : "error.postComment"),
          ),
        );
        return false;
      }
    },
    [modelLabel, recordId, postKind, replyToMessage, postMessage, t],
  );

  async function handleFollowToggle(): Promise<void> {
    if (!modelLabel || !recordId) return;
    setError(null);
    try {
      await setFollowing({
        modelLabel,
        recordId,
        following: !isFollowing,
        notificationPolicy: "inbox",
        subtypeKeys: !isFollowing ? selectedSubtypeKeys : [],
      });
    } catch (cause) {
      setError(errorMessage(cause, t("error.following")));
    }
  }

  async function handleSubtypeToggle(subtypeKey: string, checked: boolean): Promise<void> {
    if (!modelLabel || !recordId || !isFollowing) return;
    const selected = new Set(selectedSubtypeKeys);
    if (checked) selected.add(subtypeKey);
    else selected.delete(subtypeKey);
    const nextSubtypeKeys = subtypeOptions
      .map((subtype) => subtype.key)
      .filter((key) => selected.has(key));
    if (nextSubtypeKeys.length === 0) return;
    setError(null);
    try {
      await setFollowing({
        modelLabel,
        recordId,
        following: true,
        notificationPolicy: String(selfFollower?.notification_policy ?? "inbox").toLowerCase(),
        subtypeKeys: nextSubtypeKeys,
      });
    } catch (cause) {
      setError(errorMessage(cause, t("error.filters")));
    }
  }

  async function handleMarkRead(): Promise<void> {
    if (!modelLabel || !recordId) return;
    setError(null);
    try {
      await markRead({ modelLabel, recordId });
    } catch (cause) {
      setError(errorMessage(cause, t("error.markRead")));
    }
  }

  if (!enabled) {
    return (
      <EmptyState
        icon="comments"
        title={t("chatter.noRecord")}
        description={t("chatter.noRecordHint")}
        className="min-h-48 p-4"
      />
    );
  }
  if (threadQuery.fetching && threadQuery.data === undefined) {
    return <LoadingPanel message={t("chatter.loading")} />;
  }
  if (threadQuery.error || threadPayload?.error_code === "BAD_RECORD") {
    return (
      <EmptyState
        icon="comments"
        title={t("chatter.disabled")}
        description={t("chatter.disabledHint")}
        className="min-h-48 p-4"
      />
    );
  }

  return (
    <div className="flex min-h-72 flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <div className={cn(textRoleVariants({ role: "meta" }), "inline-flex items-center gap-1")}>
          <Glyph decorative name="users" />
          {t("chatter.following", { count: followerCount })}
          {attachmentCount > 0 ? (
            <>
              <span aria-hidden="true">·</span>
              <Glyph decorative name="attachment" />
              {attachmentCount.toLocaleString()}
            </>
          ) : null}
          {unreadCount > 0 ? (
            <>
              <span aria-hidden="true">·</span>
              <Glyph decorative name="bell" />
              {t("chatter.unread", { count: unreadCount })}
            </>
          ) : null}
          {deliveryErrorCount > 0 ? (
            <>
              <span aria-hidden="true">·</span>
              <Glyph decorative name="triangle-alert" />
              {t("chatter.failed", { count: deliveryErrorCount })}
            </>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          {unreadCount > 0 ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={markReadState.fetching}
              onClick={() => void handleMarkRead()}
            >
              <Glyph name="check" />
              {t("chatter.markRead")}
            </Button>
          ) : null}
          <Button
            type="button"
            variant={isFollowing ? "secondary" : "ghost"}
            size="sm"
            disabled={followState.fetching}
            onClick={() => void handleFollowToggle()}
          >
            <Glyph name={isFollowing ? "bell-off" : "bell"} />
            {isFollowing ? t("chatter.unfollow") : t("chatter.follow")}
          </Button>
        </div>
      </div>
      {isFollowing && subtypeOptions.length > 0 ? (
        <div className="flex flex-wrap gap-2" role="group" aria-label={t("subtype.legend")}>
          {subtypeOptions.map((subtype) => {
            const checked = selectedSubtypeKeys.includes(subtype.key);
            return (
              <FieldRoot
                key={subtype.key}
                className="inline-flex h-8 items-center gap-2 rounded-6 border border-border-subtle bg-surface px-2 text-12 text-fg-muted"
                title={subtype.description || subtype.name}
              >
                <FieldRoot.Item>
                  <Checkbox
                    size="sm"
                    checked={checked}
                    disabled={followState.fetching || (checked && selectedSubtypeKeys.length === 1)}
                    onCheckedChange={(next) => void handleSubtypeToggle(subtype.key, next)}
                  />
                  <FieldRoot.Label className="text-12 text-fg-muted">
                    {subtype.name}
                  </FieldRoot.Label>
                </FieldRoot.Item>
              </FieldRoot>
            );
          })}
        </div>
      ) : null}
      <SearchInput
        value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
        onClear={() => setSearch("")}
        placeholder={t("chatter.search")}
        aria-label={t("chatter.search")}
      />
      {messages.length > 0 ? (
        <div className="space-y-3">
          {debouncedSearch ? (
            <div className={cn(textRoleVariants({ role: "caption" }), "px-1")}>
              {t("chatter.results", { count: messageResultCount })}
            </div>
          ) : null}
          <MessageFeed label={t("chatter.feedLabel")}>
            {messages.map((message) => (
              <MessageFeedRow
                key={message.id}
                message={message}
                editing={editingMessageId === message.id}
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
          title={debouncedSearch ? t("chatter.noMatchTitle") : t("chatter.emptyTitle")}
          description={
            debouncedSearch ? t("chatter.noMatchHint") : t("chatter.emptyHint")
          }
          className="min-h-40 p-4"
        />
      )}
      <ChatterComposer
        t={t}
        postKind={postKind}
        onPostKindChange={setPostKind}
        replyToMessage={replyToMessage}
        onClearReply={() => setReplyToMessage(null)}
        recipientOptions={recipientOptions}
        recipientsLoading={recipientUsersQuery.fetching}
        posting={postState.fetching}
        onPost={handlePost}
        onError={setError}
      />
      {error ? (
        <p className={cn(textRoleVariants({ role: "caption" }), "text-danger-text")}>{error}</p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Composer — its own child so body keystrokes never re-render the message rows.
// ---------------------------------------------------------------------------

interface ChatterComposerProps {
  t: MessagingT;
  postKind: ChatterPostKind;
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
  postKind,
  onPostKindChange,
  replyToMessage,
  onClearReply,
  recipientOptions,
  recipientsLoading,
  posting,
  onPost,
  onError,
}: ChatterComposerProps): React.ReactElement {
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
  const hasFailedUpload = taskRows.some((task) => task.status === "failed");
  const hasComposerAttachments = attachmentDrafts.length > 0 || taskRows.length > 0;
  const canSubmit = body.trim() !== "" || attachmentDrafts.length > 0;

  function handleKindChange(next: ChatterPostKind): void {
    onPostKindChange(next);
    if (next === "note") {
      setSelectedRecipientIds([]);
      setAutofollowRecipients(false);
    }
  }

  function handleFiles(files: FileList | readonly File[] | null): void {
    if (!files || files.length === 0) return;
    onError(null);
    uploads.upload(Array.from(files));
  }

  async function submit(): Promise<void> {
    const next = body.trim();
    const attachmentIds = attachmentDrafts.map((file) => file.id);
    if (!next && attachmentIds.length === 0) return;
    // Clamp to the options actually offered — a recipient may have dropped off the
    // suggestion/follower list between selection and submit.
    const availableIds = new Set(recipientOptions.map((option) => option.id));
    const recipientUserIds = selectedRecipientIds.filter((id) => availableIds.has(id));
    const ok = await onPost({
      body: next,
      attachmentIds,
      recipientUserIds,
      autofollowRecipients,
    });
    if (!ok) return;
    setBody("");
    setSelectedRecipientIds([]);
    setAutofollowRecipients(false);
    setAttachmentDrafts([]);
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    // The feed composer is a plain form (no assistant-ui ComposerPrimitive), so wire
    // Enter-to-submit here; Shift+Enter keeps the newline. Skip while an IME is composing.
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!posting && !uploadBusy && canSubmit) void submit();
  }

  const selectedRecipients = recipientOptions.filter((option) =>
    selectedRecipientIds.includes(option.id),
  );
  const availableRecipients = recipientOptions.filter(
    (option) => !selectedRecipientIds.includes(option.id),
  );

  return (
    <form
      className="mt-auto"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <UploadDropTarget
        disabled={posting}
        overlay={t("composer.dropFiles")}
        overlayClassName="rounded-6"
        onFiles={handleFiles}
      >
        <MessageComposer
          hint={<MessageComposerHint />}
          attachments={
            hasComposerAttachments ? (
              <>
                {attachmentDrafts.map((file) => (
                  <MessageAttachmentChip
                    key={file.id}
                    icon={<Glyph decorative name="attachment" />}
                    remove={
                      <Button
                        type="button"
                        variant="ghost"
                        size="iconSm"
                        aria-label={t("composer.removeAttachment", { name: file.filename })}
                        onClick={() =>
                          setAttachmentDrafts((current) =>
                            current.filter((item) => item.id !== file.id),
                          )
                        }
                      >
                        <Glyph name="x" />
                      </Button>
                    }
                  >
                    {file.filename}
                  </MessageAttachmentChip>
                ))}
                {taskRows.map((task) => (
                  <MessageAttachmentChip
                    key={task.id}
                    tone={task.status === "failed" ? "danger" : "neutral"}
                    icon={<Glyph decorative name="attachment" />}
                    remove={<span className="text-2xs">{uploadTaskLabel(task.status, t)}</span>}
                  >
                    {task.name}
                  </MessageAttachmentChip>
                ))}
                {hasFailedUpload ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="iconSm"
                    aria-label={t("composer.clearUploads")}
                    onClick={uploads.clearFinished}
                  >
                    <Glyph name="x" />
                  </Button>
                ) : null}
              </>
            ) : null
          }
          input={
            <div className="space-y-2">
              {replyToMessage ? (
                <div className="flex items-center gap-2 rounded-6 border border-border-subtle bg-surface px-2 py-1.5">
                  <Glyph decorative name="quote" className="shrink-0 text-fg-muted" />
                  <div className="min-w-0 flex-1">
                    <div className={cn(textRoleVariants({ role: "caption" }), "font-medium")}>
                      {t("message.replyingTo", { kind: replyKindLabel(replyToMessage, t) })}
                    </div>
                    <div className="truncate text-13 text-fg">{messageText(replyToMessage)}</div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="iconSm"
                    aria-label={t("composer.cancelReply")}
                    onClick={onClearReply}
                  >
                    <Glyph name="x" />
                  </Button>
                </div>
              ) : null}
              <SegmentedControl<ChatterPostKind>
                value={postKind}
                onValueChange={handleKindChange}
                options={[
                  { value: "comment", label: t("composer.comment") },
                  { value: "note", label: t("composer.note") },
                ]}
              />
              {postKind === "comment" ? (
                <ComposerRecipients
                  t={t}
                  selected={selectedRecipients}
                  available={availableRecipients}
                  loading={recipientsLoading}
                  autofollow={autofollowRecipients}
                  onAdd={(id) =>
                    setSelectedRecipientIds((current) =>
                      current.includes(id) ? current : [...current, id],
                    )
                  }
                  onRemove={(id) =>
                    setSelectedRecipientIds((current) => current.filter((item) => item !== id))
                  }
                  onAutofollowChange={setAutofollowRecipients}
                />
              ) : null}
              <Textarea
                value={body}
                onChange={(event) => setBody(event.currentTarget.value)}
                onKeyDown={handleKeyDown}
                rows={3}
                resize="none"
                className={messageComposerInputClassName}
                aria-label={t("composer.messageLabel")}
                placeholder={
                  postKind === "note"
                    ? t("composer.logNote")
                    : t("composer.writeComment")
                }
              />
            </div>
          }
          actions={
            <>
              <Button
                type="button"
                variant="ghost"
                size="iconSm"
                aria-label={t("composer.attach")}
                title={t("composer.attach")}
                disabled={posting}
                onClick={() => fileInputRef.current?.click()}
              >
                <Glyph name="attachment" />
              </Button>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={posting || uploadBusy || !canSubmit}
              >
                <Glyph name="send" />
                {postKind === "note" ? t("composer.log") : t("composer.send")}
              </Button>
            </>
          }
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = "";
          }}
        />
      </UploadDropTarget>
    </form>
  );
}

function ComposerRecipients({
  t,
  selected,
  available,
  loading,
  autofollow,
  onAdd,
  onRemove,
  onAutofollowChange,
}: {
  t: MessagingT;
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
          disabled={loading || available.length === 0}
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
  t: MessagingT;
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
  t,
  onStartEdit,
  onCancelEdit,
  onSaveEdit,
  onStartReply,
  onDelete,
  onToggleReaction,
  onToggleStarred,
  onMarkDone,
}: MessageFeedRowProps): React.ReactElement {
  const text = messageText(message);
  const author = message.sender?.display_name || message.sender?.value || t("message.author");
  const trackingValues = [...message.tracking_values].sort(
    (left, right) =>
      left.position - right.position || left.field_label.localeCompare(right.field_label),
  );
  const attachments = message.parts
    .map((part) => part.file)
    .filter((file): file is NonNullable<typeof file> => file !== null);
  const timestamp = message.sent_at ?? message.created_at;
  const subtypeDescription = message.subtype?.description || message.subtype?.name || "";
  const directionTag = directionLabel(message.direction, t);
  const reactions: Reaction[] = message.reaction_groups.map((group) => ({
    reaction: group.reaction,
    count: group.count,
    active: group.self_reacted,
    title: reactionTitle(group),
  }));
  const activeReactions = message.reaction_groups
    .filter((group) => group.self_reacted)
    .map((group) => group.reaction);

  if (editing) {
    return (
      <MessageRow
        avatar={<Avatar size="sm" initials={avatarInitials(author)} alt={author} />}
        author={author}
      >
        <MessageEditor
          initialBody={text}
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
      channel={directionTag ? <Tag tone="info" density="micro">{directionTag}</Tag> : undefined}
      meta={message.status === "EDITED" ? t("chatter.editedMeta") : undefined}
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
      attachments={
        attachments.length > 0
          ? attachments.map((file) => (
              <a key={file.id} href={file.url} download={file.filename} className="block max-w-full">
                <MessageAttachmentChip
                  icon={<Glyph decorative name="attachment" />}
                  remove={
                    <span className="shrink-0 text-2xs text-fg-subtle">
                      {formatSize(file.size_bytes)}
                    </span>
                  }
                >
                  {file.title || file.filename}
                </MessageAttachmentChip>
              </a>
            ))
          : undefined
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
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            aria-label={t("message.reply")}
            onClick={() => onStartReply(message)}
          >
            <Glyph name="quote" />
          </Button>
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
          {message.can_edit ? (
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
      {subtypeDescription && message.message_type !== "COMMENT" ? (
        <div className={cn(textRoleVariants({ role: "caption" }), "mb-1 font-medium")}>
          {subtypeDescription}
        </div>
      ) : null}
      {text ? <span>{text}</span> : null}
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

function messageText(message: Pick<RecordMessageRow, "parts" | "preview">): string {
  const part = message.parts.find((item) => item.fragment?.text);
  return part?.fragment?.text ?? message.preview ?? "";
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

function reactionTitle(group: RecordMessageRow["reaction_groups"][number]): string {
  const names = group.handles
    .map((handle) => handle.display_name || handle.value)
    .filter((value) => value.trim() !== "");
  if (names.length === 0) return `${group.reaction} ${group.count.toLocaleString()}`;
  return `${group.reaction} by ${names.join(", ")}`;
}

function subtypeKeysFromValue(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function recipientOptionsFrom(
  users: readonly RecipientUserRow[],
  followers: ReadonlyArray<NonNullable<RecordThreadPayload["followers"]>[number]>,
  suggestions: readonly SuggestedRecipientRow[],
): readonly RecipientOption[] {
  const byId = new Map<string, RecipientOption>();
  for (const user of users) {
    if (user.is_active === false) continue;
    byId.set(user.id, {
      id: user.id,
      label: user.display_name || user.username || user.email || "User",
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
      label: user.display_name || user.username || user.email || "User",
      detail: previous?.detail || user.email || user.username || "",
      follower: previous?.follower ?? false,
      suggested: true,
      reason: suggestion.reason || "Suggested",
    });
  }
  for (const follower of followers) {
    const user = follower.user;
    const previous = byId.get(user.id);
    byId.set(user.id, {
      id: user.id,
      label: user.display_name || user.username || "User",
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

function uploadTaskLabel(status: UploadTask["status"], t: MessagingT): string {
  switch (status) {
    case "hashing":
      return t("upload.preparing");
    case "uploading":
      return t("upload.uploading");
    case "finalizing":
      return t("upload.finalizing");
    case "failed":
      return t("upload.failed");
    case "deduped":
    case "done":
      return t("upload.attached");
  }
}
