import { useState } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { MessageReader } from "@angee/messaging";
import { senderDisplayName } from "@angee/parties";
import {
  Alert,
  Button,
  Collapsible,
  EmptyState,
  Glyph,
  LoadingPanel,
  MetaGrid,
  PageHeader,
  Tag,
} from "@angee/ui";
import { InboxMessage } from "./documents";
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
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const message = query.data?.inbox_message.message;
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
        title={message?.title || t("inbox.message")}
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
                  {message.thread.conversation_label || t("inbox.conversation")}
                </Button>
                <Glyph name="chevron-right" size={12} />
              </>
            ) : null}
            <span>{t("inbox.message")}</span>
          </nav>
        }
        actions={
          <Button size="sm" variant="secondary" onClick={navigation.back}>
            <Glyph name="chevron-left" />
            {backLabel}
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
      ) : (
        <div className="min-h-0 flex-1 overflow-auto p-6">
          <MessageReader
            message={message}
            activePartId={navigation.part}
            previewPartId={navigation.tab === "preview" ? navigation.part : undefined}
            onPreviewPartChange={(part) =>
              navigation.patch(part ? { part, tab: "preview" } : { tab: undefined })
            }
            actions={
              <Button size="sm" variant="ghost" onClick={() => void copyLink()}>
                <Glyph name="link" />
                {t(copied ? "inbox.copied" : "inbox.copyLink")}
              </Button>
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
                      onClick={() => navigation.connect(use.target, message.id)}
                    >
                      <Glyph name="link" size={13} />
                      {t("inbox.accessibleUses", { count: use.count })}
                    </Button>
                  ))}
              </div>
            )}
          >
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
          </MessageReader>
        </div>
      )}
    </section>
  );
}
