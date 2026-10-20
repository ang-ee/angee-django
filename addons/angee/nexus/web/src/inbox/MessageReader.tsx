import { useMemo, useState } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { useModelMetadata, type Row } from "@angee/metadata";
import { MessageReader } from "@angee/messaging";
import { senderDisplayName } from "@angee/parties";
import {
  ActionTrigger,
  Alert,
  Button,
  Collapsible,
  CommandBand,
  ErrorBanner,
  formatDateTime,
  Glyph,
  LoadingPanel,
  MetaGrid,
  RecordHeader,
  optionLabel,
  useEnumOptions,
  type RecordChromeContext,
} from "@angee/ui";

const MESSAGE_MODEL = "messaging.Message";
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
  const metadata = useModelMetadata(MESSAGE_MODEL);
  const directions = useEnumOptions(MESSAGE_MODEL, "direction", { casing: "upper" });
  const statuses = useEnumOptions(MESSAGE_MODEL, "status", { casing: "upper" });
  const platforms = useEnumOptions(MESSAGE_MODEL, "platform", { casing: "upper" });
  // The open message is a record: its header controls come through form#chrome like any other.
  const chrome = useMemo<RecordChromeContext | null>(() => message ? {
    resource: MESSAGE_MODEL,
    dataProviderName: metadata?.resource.schemaName,
    canonicalResource: MESSAGE_MODEL,
    recordId: message.id,
    record: message as unknown as Row,
    formReadOnly: true,
  } : null, [message, metadata?.resource.schemaName]);
  const sender = message?.sender ? senderDisplayName(message.sender) : undefined;
  const channel = message?.channel?.display_name || (message?.platform ? optionLabel(platforms, message.platform) : undefined);
  const direction = message ? optionLabel(directions, message.direction) : undefined;
  return (
    <section
      aria-label={t("inbox.message")}
      className="flex h-full min-h-0 flex-col"
    >
      <CommandBand
        menu={message ? <>
          <ActionTrigger glyph="link" onClick={() => void copyLink()}>{t(copied ? "inbox.copied" : "inbox.copyLink")}</ActionTrigger>
          <ActionTrigger glyph="users" onClick={() => navigation.connect(`participants:${message.id}`, message.id)}>{t("inbox.participants")}</ActionTrigger>
          <ActionTrigger glyph="link" onClick={() => navigation.connect(`relations:${message.id}`, message.id)}>{t("inbox.relations")}</ActionTrigger>
          {message.sender?.party_link_confirmed && message.sender.party ? (
            <ActionTrigger onClick={() => navigation.connect(`handles:${message.sender!.party!.id}`, message.id)}>{t("inbox.handles")}</ActionTrigger>
          ) : null}
          {message.sender ? (
            <ActionTrigger onClick={() => navigation.select(
              message.sender!.party_link_confirmed && message.sender!.party ? `party:${message.sender!.party.id}` : `handle:${message.sender!.id}`,
            )}>{t("inbox.exploreSender")}</ActionTrigger>
          ) : null}
        </> : null}
        verbs={<>
          <Button size="sm" variant="ghost" onClick={navigation.back}>
            <Glyph name="chevron-left" />
            {backLabel}
          </Button>
          {message?.thread ? (
            <Button size="sm" variant="primary" onClick={() => navigation.conversation(message.thread!.id, message.id)}>
              {t("inbox.openConversation")}
            </Button>
          ) : null}
        </>}
        chrome={chrome}
      />
      <RecordHeader
        density="compact"
        headingLevel={1}
        crumbs={[t("inbox.message"), channel, direction].filter(Boolean).join(" · ")}
        title={message?.title || t("inbox.message")}
        meta={message ? [sender, message.received_at ? formatDateTime(message.received_at) : undefined].filter(Boolean).join(" · ") : undefined}
        status={message && !message.thread ? { label: t("inbox.standalone") } : undefined}
      />
      {query.isPending ? (
        <LoadingPanel />
      ) : query.error || !message ? (
        <div className="grid gap-3 p-4">
          <ErrorBanner title={t("inbox.unavailable")} description={query.error?.message ?? t("inbox.unavailableHint")} />
          <div><Button size="sm" variant="secondary" onClick={() => void query.refetch()}>{t("inbox.retry")}</Button></div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto p-6">
          <MessageReader
            message={message}
            activePartId={navigation.part}
            onPreviewPartChange={(partId) => {
              const file = message.parts.find((part) => part.id === partId)?.file;
              if (file) navigation.previewFile(file.id, message.id);
            }}
            renderPartActions={(part) => (
              <div className="flex flex-wrap gap-2">
                {query.data?.inbox_message.uses
                  .filter((use) => use.part_id === part.id)
                  .map((use) => (
                    <Button
                      key={use.target}
                      size="sm"
                      variant="link"
                      onClick={() => navigation.connect(use.target, message.id)}
                    >
                      <Glyph name="link" />
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
            <ErrorBanner description={actionError ?? null} />
            {message.sender?.party && !message.sender.party_link_confirmed ? (
              <Alert tone="info">
                {t("inbox.suggested")}: {message.sender.party.display_name}
              </Alert>
            ) : null}
            <Collapsible.Root>
              <Collapsible.Trigger className="text-13 font-medium">
                {t("inbox.details")}
              </Collapsible.Trigger>
              <Collapsible.Panel className="space-y-3 pt-3">
                {/* The envelope's labelled facts; the reader's own header names the participants. */}
                <MetaGrid
                  rows={[
                    [t("inbox.account"), message.channel?.display_name ?? "—"],
                    [t("inbox.direction"), direction ?? "—"],
                    [t("inbox.status"), optionLabel(statuses, message.status)],
                    [t("inbox.received"), message.received_at ? formatDateTime(message.received_at) : "—"],
                  ]}
                />
              </Collapsible.Panel>
            </Collapsible.Root>
          </MessageReader>
        </div>
      )}
    </section>
  );
}
