import { useAuthoredMutation } from "@angee/refine";
import * as React from "react";
import {
  Button, Checkbox, Dialog, EmptyState, FieldRoot, PaneToolbar, PaneToolbarIconButton, PaneToolbarMenu, ScopeChip, SearchInput, Select,
  errorMessage, textRoleVariants,
} from "@angee/ui";
import type { ChatterSelection, ChatterViewContext } from "@angee/ui/runtime";

import { useMessagingT, type MessagingT } from "./i18n";
import { READ_MODELS, SetRecordFollowingDocument, type RecordThreadPayload } from "./documents";
import {
  RecordThreadConversation,
  type RecordThreadConversationChrome,
  type RecordThreadConversationProps,
} from "./RecordThreadConversation";

export interface RecordChatterPaneProps {
  context: ChatterViewContext;
  selection?: ChatterSelection;
  submitKey?: RecordThreadConversationProps["submitKey"];
  messageLimit?: RecordThreadConversationProps["messageLimit"];
}

/** The Discussion's scope: everything, comments, or internal notes (server message types). */
const SCOPES = [
  { id: "all", key: "chatter.scope.all", types: [] as readonly string[] },
  { id: "comments", key: "chatter.scope.comments", types: ["comment", "email"] },
  { id: "notes", key: "chatter.scope.notes", types: ["notification"] },
] as const;
type ScopeId = (typeof SCOPES)[number]["id"];

/** The record's Discussion: its thread, the pane toolbar and composer. Discuss rooms compose the same conversation owner. */
export function RecordChatterPane({ context, selection, submitKey, messageLimit }: RecordChatterPaneProps): React.ReactElement {
  const t = useMessagingT();
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  const [scope, setScope] = React.useState<ScopeId>("all");
  const types = SCOPES.find((entry) => entry.id === scope)?.types;
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
      around={selection?.focus === "message" ? selection.target : undefined}
      submitKey={submitKey}
      messageLimit={messageLimit}
      searchPlacement="header"
      messageTypes={types}
      header={(chrome) => (
        <DiscussionHead
          {...chrome}
          modelLabel={modelLabel}
          recordId={recordId}
          scope={scope}
          onScope={setScope}
          t={t}
        />
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// The Discussion's head: the pane toolbar (scope chips, search, mark read, the
// notification settings behind the overflow) and the search row it reveals.
// Rendered through the conversation's `header` seam; a discuss room omits it.
// ---------------------------------------------------------------------------

interface DiscussionHeadProps extends RecordThreadConversationChrome {
  modelLabel: string;
  recordId: string;
  scope: ScopeId;
  onScope: (scope: ScopeId) => void;
  t: MessagingT;
}

function DiscussionHead({ payload, markRead, markReadPending, reportError, search, setSearch, modelLabel, recordId, scope, onScope, t }: DiscussionHeadProps): React.ReactElement {
  const [searchOpen, setSearchOpen] = React.useState(false);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const unreadCount = payload?.unread_count ?? 0;
  const isFollowing = Boolean(payload?.is_following);
  return (
    <>
      <PaneToolbar
        scope={SCOPES.map((entry) => (
          <ScopeChip key={entry.id} label={t(entry.key)} active={scope === entry.id} onClick={() => onScope(entry.id)} />
        ))}
        search={<PaneToolbarIconButton icon="search" label={t("chatter.search")} active={searchOpen || Boolean(search)} onClick={() => {
          if (searchOpen) setSearch("");
          setSearchOpen((open) => !open);
        }} />}
        actions={unreadCount > 0 ? [{ icon: "check", label: t("chatter.markRead"), badge: unreadCount, disabled: markReadPending, onClick: () => void markRead() }] : undefined}
        overflow={isFollowing ? <PaneToolbarMenu label={t("chatter.more")} items={[{ icon: "bell", label: t("chatter.notificationSettings"), onClick: () => setSettingsOpen(true) }]} /> : false}
      />
      {searchOpen ? (
        <SearchInput
          autoFocus
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          onClear={() => setSearch("")}
          placeholder={t("chatter.search")}
          aria-label={t("chatter.search")}
        />
      ) : null}
      {settingsOpen && payload ? (
        <NotificationSettingsDialog payload={payload} modelLabel={modelLabel} recordId={recordId} reportError={reportError} onClose={() => setSettingsOpen(false)} t={t} />
      ) : null}
    </>
  );
}

const POLICY_KEYS = { inbox: "chatter.policy.inbox", email: "chatter.policy.email", none: "chatter.policy.none" } as const;

/** What the viewer is notified about on this record: the follow's subtypes and channel. */
function NotificationSettingsDialog({ payload, modelLabel, recordId, reportError, onClose, t }: {
  payload: RecordThreadPayload;
  modelLabel: string;
  recordId: string;
  reportError: (message: string | null) => void;
  onClose: () => void;
  t: MessagingT;
}): React.ReactElement {
  const [setFollowing, followState] = useAuthoredMutation(SetRecordFollowingDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.set_record_following,
  });
  const selfFollower = payload.self_follower ?? null;
  const subtypeOptions = React.useMemo(() => [...(payload.subtypes ?? [])].filter((subtype) => !subtype.internal), [payload.subtypes]);
  const selectedSubtypeKeys = React.useMemo(() => {
    const explicit = subtypeKeysFromValue(selfFollower?.subtype_keys);
    if (explicit.length > 0) return explicit;
    return subtypeOptions.filter((subtype) => subtype.default).map((subtype) => subtype.key);
  }, [selfFollower?.subtype_keys, subtypeOptions]);
  const policy = String(selfFollower?.notification_policy ?? "inbox").toLowerCase();

  async function save(nextSubtypeKeys: readonly string[], nextPolicy: string): Promise<void> {
    reportError(null);
    try {
      await setFollowing({ modelLabel, recordId, following: true, notificationPolicy: nextPolicy, subtypeKeys: [...nextSubtypeKeys] });
    } catch (cause) {
      reportError(errorMessage(cause, t("error.filters")));
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="sm">
          <Dialog.Header>
            <Dialog.Title>{t("chatter.notificationSettings")}</Dialog.Title>
            <Dialog.Close />
          </Dialog.Header>
          <Dialog.Body className="space-y-4">
            <p className={textRoleVariants({ role: "description" })}>{t("chatter.notificationSettingsHint")}</p>
            <FieldRoot>
              <FieldRoot.Label>{t("chatter.notificationChannel")}</FieldRoot.Label>
              <Select
                aria-label={t("chatter.notificationChannel")}
                value={policy}
                options={Object.entries(POLICY_KEYS).map(([value, key]) => ({ value, label: t(key) }))}
                onValueChange={(value) => void save(selectedSubtypeKeys, value)}
              />
            </FieldRoot>
            {subtypeOptions.length > 0 ? (
              <div className="space-y-2" role="group" aria-label={t("subtype.legend")}>
                <p className={textRoleVariants({ role: "caption" })}>{t("subtype.legend")}</p>
                {subtypeOptions.map((subtype) => {
                  const checked = selectedSubtypeKeys.includes(subtype.key);
                  return (
                    <FieldRoot key={subtype.key} title={subtype.description || subtype.name}>
                      <FieldRoot.Item>
                        <Checkbox
                          size="sm"
                          checked={checked}
                          disabled={followState.fetching || (checked && selectedSubtypeKeys.length === 1)}
                          onCheckedChange={(next) => {
                            const selected = new Set(selectedSubtypeKeys);
                            if (next) selected.add(subtype.key); else selected.delete(subtype.key);
                            const keys = subtypeOptions.map((option) => option.key).filter((key) => selected.has(key));
                            if (keys.length > 0) void save(keys, policy);
                          }}
                        />
                        <FieldRoot.Label className="text-13">{subtype.name}</FieldRoot.Label>
                      </FieldRoot.Item>
                    </FieldRoot>
                  );
                })}
              </div>
            ) : null}
            <div className="flex justify-end">
              <Button type="button" size="sm" variant="secondary" onClick={onClose}>{t("chatter.done")}</Button>
            </div>
          </Dialog.Body>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function subtypeKeysFromValue(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}
