import { useMemo, useState } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { useModelMetadata, type Row } from "@angee/metadata";
import { MessageReader } from "@angee/messaging";
import {
  ActionMenu,
  ActionTrigger,
  Alert,
  Button,
  Collapsible,
  ErrorBanner,
  formatDateTime,
  Glyph,
  LoadingPanel,
  MetaGrid,
  RecordHeader,
  RecordChrome,
  optionLabel,
  useEnumOptions,
  type RecordChromeContext,
} from "@angee/ui";

import { InboxMessage } from "./documents";
import { useNexusT } from "../i18n";
import { INBOX_MODELS, type InboxNavigation, type InboxScope } from "./state";
import { useInboxBreadcrumbs } from "./breadcrumbs";

const MESSAGE_MODEL = "messaging.Message";

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
    // The reader shows a missing message itself (the banner with Retry), so the read raises no toast.
    { models: INBOX_MODELS, enabled: Boolean(navigation.message), errorNotification: false },
  );
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const message = query.data?.inbox_message.message;
  useInboxBreadcrumbs(navigation, {
    message: message?.title || t("inbox.message"),
    conversation: message?.thread ? { id: message.thread.id, label: message.thread.conversation_label } : undefined,
  });
  const uses = useMemo(() => {
    // The server count is already distinct messages per target, not per part.
    const byTarget = new Map((query.data?.inbox_message.uses ?? [])
      .filter((use) => use.target.startsWith("file:") || use.count > 1)
      .map((use) => [use.target, use]));
    return [...byTarget.values()].map((use) => {
      const part = message?.parts.find((part) => part.id === use.part_id);
      return { ...use, label: part?.name || part?.fragment?.text || t("inbox.sharedText") };
    });
  }, [query.data?.inbox_message.uses, message?.parts, t]);
  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  }
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
  const direction = message ? optionLabel(directions, message.direction) : undefined;
  return (
    <section
      aria-label={t("inbox.message")}
      className="flex h-full min-h-0 flex-col"
    >
      <RecordHeader
        density="compact"
        headingLevel={1}
        crumbs={message?.thread?.conversation_label}
        title={message?.title || t("inbox.details")}
        status={message && !message.thread ? { label: t("inbox.standalone") } : undefined}
        actions={message ? <>
          <ActionMenu label={null} aria-label={t("inbox.messageActions")} glyph="more-vertical" size="iconSm">
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
          </ActionMenu>
          {chrome ? <RecordChrome value={chrome} /> : null}
        </> : undefined}
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
            onOpenReply={navigation.openInContext}
            activePartId={navigation.part}
            onPreviewPartChange={(partId) => {
              const file = message.parts.find((part) => part.id === partId)?.file;
              if (file) navigation.previewFile(file.id, message.id);
            }}
          >
            {/* One labelled action per exact content target, with its distinct message count. */}
            {uses.length ? (
              <div className="flex flex-wrap gap-2">
                {uses.map((use) => (
                  <Button key={use.target} size="sm" variant="secondary" className="max-w-full" onClick={() => navigation.connect(use.target, message.id)}>
                    <Glyph name="link" />
                    <span className="max-w-48 truncate">{use.label}</span>
                    {t("inbox.accessibleUses", { count: use.count })}
                  </Button>
                ))}
              </div>
            ) : null}
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
                    [t("inbox.platform"), optionLabel(platforms, message.platform)],
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
