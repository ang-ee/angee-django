import { useState } from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { senderDisplayName } from "@angee/parties";
import {
  Alert,
  Avatar,
  Button,
  Collapsible,
  EmptyState,
  Glyph,
  LoadingPanel,
  MessagePartsView,
  MetaGrid,
  PageHeader,
  PreviewPane,
  RelativeTime,
  Tag,
  avatarInitials,
} from "@angee/ui";
import { InboxMessage, SetInboxStar } from "./documents";
import { useNexusT } from "../i18n";
import { INBOX_MODELS, type InboxNavigation, type InboxScope } from "./state";

/** A message replaces the results or transcript while their route state stays intact. */
export function InboxMessageReader({
  scope,
  navigation,
}: {
  scope: InboxScope;
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  const query = useAuthoredQuery(
    InboxMessage,
    { ...scope, id: navigation.message },
    { models: INBOX_MODELS, enabled: Boolean(navigation.message) },
  );
  const [star, starState] = useAuthoredMutation(SetInboxStar, {
    invalidateModels: ["messaging.MessageStar", "messaging.Message"],
  });
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const message = query.data?.inbox_message.message;
  const preview =
    navigation.tab === "preview"
      ? message?.parts.find((part) => part.id === navigation.part)
      : undefined;
  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  }
  const backLabel = navigation.thread
    ? t("inbox.backConversation")
    : t("inbox.backResults");
  return (
    <section
      aria-label={t("inbox.message")}
      className="flex h-full min-h-0 flex-col"
    >
      <PageHeader
        density="compact"
        title={
          preview
            ? preview.name || t("inbox.preview")
            : message?.title || t("inbox.message")
        }
        description={
          <nav aria-label="Breadcrumb" className="flex items-center gap-1">
            <Button size="sm" variant="ghost" onClick={navigation.results}>
              {t("inbox.results")}
            </Button>
            <Glyph name="chevron-right" size={12} />
            {message?.thread ? (
              <>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    navigation.conversation(message.thread!.id, message.id)
                  }
                >
                  {message.thread.title?.text || t("inbox.conversation")}
                </Button>
                <Glyph name="chevron-right" size={12} />
              </>
            ) : null}
            <span>{t("inbox.message")}</span>
          </nav>
        }
        actions={
          <Button
            size="sm"
            variant="secondary"
            onClick={
              preview
                ? () => navigation.patch({ tab: undefined })
                : navigation.back
            }
          >
            <Glyph name="chevron-left" />
            {preview ? t("inbox.closePreview") : backLabel}
          </Button>
        }
      />
      {query.isPending ? (
        <LoadingPanel />
      ) : query.error || !message ? (
        <EmptyState
          title={t("inbox.unavailable")}
          description={query.error?.message}
          actions={
            <Button onClick={() => void query.refetch()}>
              {t("inbox.retry")}
            </Button>
          }
        />
      ) : preview?.file?.url ? (
        <div className="min-h-0 flex-1 overflow-auto p-4">
          <PreviewPane
            file={{
              url: preview.file.url,
              name: preview.name || preview.file.filename || t("inbox.file"),
              mime: preview.type || preview.file.mime_type?.mime_type,
              size: preview.file.size_bytes,
            }}
          />
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto p-6">
          <div className="mx-auto max-w-3xl space-y-5">
            <div className="flex items-center gap-3">
              <Avatar
                initials={avatarInitials(
                  senderDisplayName(message.sender, t("inbox.unknownSender")),
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-13">
                  {senderDisplayName(message.sender, t("inbox.unknownSender"))}
                </div>
                <div className="truncate text-2xs text-fg-muted">
                  {message.sender?.value}
                </div>
              </div>
              <Button
                size="iconSm"
                variant={message.starred ? "secondary" : "ghost"}
                aria-label={t(message.starred ? "inbox.unstar" : "inbox.star")}
                aria-pressed={message.starred}
                disabled={starState.fetching}
                onClick={() =>
                  void star({
                    id: message.id,
                    starred: !message.starred,
                  }).catch((error) => setActionError(String(error)))
                }
              >
                <Glyph name="star" />
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void copyLink()}>
                <Glyph name="link" />
                {t(copied ? "inbox.copied" : "inbox.copyLink")}
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-2xs text-fg-muted">
              <Tag>{message.platform}</Tag>
              <span>{message.channel?.display_name}</span>
              <RelativeTime value={message.sent_at ?? message.created_at} />
            </div>
            {!query.data?.inbox_message.matches ? (
              <Alert tone="info" format="banner">
                {t("inbox.outside")}
              </Alert>
            ) : null}
            {actionError ? <Alert tone="danger">{actionError}</Alert> : null}
            {message.sender?.party && !message.sender.party_link_confirmed ? (
              <Alert tone="info">
                {t("inbox.suggested")}: {message.sender.party.display_name}
              </Alert>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  navigation.connect(`participants:${message.id}`, message.id)
                }
              >
                <Glyph name="users" />
                {t("inbox.participants")}
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  navigation.connect(`relations:${message.id}`, message.id)
                }
              >
                <Glyph name="link" />
                {t("inbox.relations")}
              </Button>
              {message.sender?.party_link_confirmed && message.sender.party ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() =>
                    navigation.connect(
                      `handles:${message.sender!.party!.id}`,
                      message.id,
                    )
                  }
                >
                  {t("inbox.handles")}
                </Button>
              ) : null}
            </div>
            <MessagePartsView
              parts={message.parts}
              resolveFileUrl={(file) => file.url}
              activePartId={navigation.part}
              onPreviewFile={(part) =>
                navigation.patch({ part: part.id ?? undefined, tab: "preview" })
              }
              renderPartActions={(part) => (
                <div className="flex flex-wrap gap-2">
                  {query.data?.inbox_message.uses
                    .filter((use) => use.part_id === part.id)
                    .map((use) => (
                      <Button
                        key={use.target}
                        size="sm"
                        variant="ghost"
                        className="h-auto px-0 text-brand"
                        onClick={() =>
                          navigation.connect(use.target, message.id)
                        }
                      >
                        <Glyph name="link" size={13} />
                        {t("inbox.accessibleUses", { count: use.count })}
                      </Button>
                    ))}
                </div>
              )}
            />
            {message.parts.length === 0 ? (
              <p className="whitespace-pre-wrap text-13">{message.preview}</p>
            ) : null}
            {message.thread ? (
              <Button
                size="sm"
                variant="secondary"
                className="w-full"
                onClick={() =>
                  navigation.conversation(message.thread!.id, message.id)
                }
              >
                {t("inbox.openConversation")}
                <Glyph name="chevron-right" />
              </Button>
            ) : (
              <Tag>{t("inbox.standalone")}</Tag>
            )}
            <Collapsible.Root>
              <Collapsible.Trigger className="text-13 font-medium">
                {t("inbox.details")}
              </Collapsible.Trigger>
              <Collapsible.Panel className="space-y-3 pt-3">
                <MetaGrid
                  rows={[
                    [t("inbox.account"), message.channel?.display_name ?? "—"],
                    [t("inbox.direction"), message.direction],
                    [t("inbox.status"), message.status],
                    [t("inbox.externalId"), message.external_id || "—"],
                    [t("inbox.received"), message.received_at ?? "—"],
                  ]}
                />
                {message.participants.map((participant) => (
                  <div key={participant.id} className="text-13">
                    <Tag>{participant.role}</Tag>{" "}
                    {senderDisplayName(participant.handle)}{" "}
                    <span className="text-fg-muted">
                      {participant.handle?.value}
                    </span>
                  </div>
                ))}
                {message.sender ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      navigation.select(
                        message.sender!.party_link_confirmed &&
                          message.sender!.party
                          ? `party:${message.sender!.party.id}`
                          : `handle:${message.sender!.id}`,
                      )
                    }
                  >
                    {t("inbox.exploreSender")}
                    <Glyph name="chevron-right" />
                  </Button>
                ) : null}
              </Collapsible.Panel>
            </Collapsible.Root>
          </div>
        </div>
      )}
    </section>
  );
}
