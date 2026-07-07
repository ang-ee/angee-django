import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import { Button, Checkbox, EmptyState, FieldRoot, Glyph, cn, errorMessage, textRoleVariants } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";

import { useMessagingT, type MessagingT } from "./i18n";
import { READ_MODELS, SetRecordFollowingDocument } from "./documents";
import {
  RecordThreadConversation,
  type RecordThreadConversationChrome,
} from "./RecordThreadConversation";

export interface RecordChatterPaneProps {
  context: ChatterViewContext;
}

/** The Comments chatter tab: the record-thread conversation (the shared
 *  `RecordThreadConversation` owner — transcript + composer + mark-read) framed by
 *  the chatter-specific chrome the pane keeps (the follower/attachment/unread meta
 *  strip, the follow toggle, and the notification-subtype filters). The transcript
 *  itself is not re-implemented here; a discuss room composes the same owner without
 *  this chrome. */
export function RecordChatterPane({ context }: RecordChatterPaneProps): React.ReactElement {
  const t = useMessagingT();
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  if (!modelLabel || !recordId) {
    return (
      <EmptyState
        icon="comments"
        title={t("chatter.noRecord")}
        description={t("chatter.noRecordHint")}
        className="min-h-48 p-4"
      />
    );
  }
  return (
    <RecordThreadConversation
      modelLabel={modelLabel}
      recordId={recordId}
      header={(chrome) => (
        <ChatterConversationHeader
          {...chrome}
          modelLabel={modelLabel}
          recordId={recordId}
          t={t}
        />
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// The chatter-specific chrome: the follower/attachment/unread meta strip, the
// mark-read + follow toggles, and the notification-subtype filters. Rendered above
// the transcript via the conversation's `header` seam; a discuss room omits it.
// ---------------------------------------------------------------------------

interface ChatterConversationHeaderProps extends RecordThreadConversationChrome {
  modelLabel: string;
  recordId: string;
  t: MessagingT;
}

function ChatterConversationHeader({
  payload,
  markRead,
  markReadPending,
  reportError,
  modelLabel,
  recordId,
  t,
}: ChatterConversationHeaderProps): React.ReactElement {
  const [setFollowing, followState] = useAuthoredMutation(SetRecordFollowingDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_following,
  });

  const followerCount = payload?.follower_count ?? 0;
  const attachmentCount = payload?.attachment_count ?? 0;
  const unreadCount = payload?.unread_count ?? 0;
  const deliveryErrorCount = payload?.message_has_error_counter ?? 0;
  const isFollowing = Boolean(payload?.is_following);
  const selfFollower = payload?.self_follower ?? null;
  const subtypeOptions = React.useMemo(
    () => [...(payload?.subtypes ?? [])].filter((subtype) => !subtype.internal),
    [payload?.subtypes],
  );
  const selectedSubtypeKeys = React.useMemo(() => {
    const explicit = subtypeKeysFromValue(selfFollower?.subtype_keys);
    if (explicit.length > 0) return explicit.map(String);
    return subtypeOptions.filter((subtype) => subtype.default).map((subtype) => subtype.key);
  }, [selfFollower?.subtype_keys, subtypeOptions]);

  async function handleFollowToggle(): Promise<void> {
    reportError(null);
    try {
      await setFollowing({
        modelLabel,
        recordId,
        following: !isFollowing,
        notificationPolicy: "inbox",
        subtypeKeys: !isFollowing ? selectedSubtypeKeys : [],
      });
    } catch (cause) {
      reportError(errorMessage(cause, t("error.following")));
    }
  }

  async function handleSubtypeToggle(subtypeKey: string, checked: boolean): Promise<void> {
    if (!isFollowing) return;
    const selected = new Set(selectedSubtypeKeys);
    if (checked) selected.add(subtypeKey);
    else selected.delete(subtypeKey);
    const nextSubtypeKeys = subtypeOptions
      .map((subtype) => subtype.key)
      .filter((key) => selected.has(key));
    if (nextSubtypeKeys.length === 0) return;
    reportError(null);
    try {
      await setFollowing({
        modelLabel,
        recordId,
        following: true,
        notificationPolicy: String(selfFollower?.notification_policy ?? "inbox").toLowerCase(),
        subtypeKeys: nextSubtypeKeys,
      });
    } catch (cause) {
      reportError(errorMessage(cause, t("error.filters")));
    }
  }

  return (
    <>
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
              disabled={markReadPending}
              onClick={() => void markRead()}
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
    </>
  );
}

function subtypeKeysFromValue(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}
