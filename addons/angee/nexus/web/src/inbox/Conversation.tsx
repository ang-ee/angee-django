import { createContext, useContext, useEffect, useState, type ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { ThreadTranscript } from "@angee/messaging";
import { Button, Chip, ErrorBanner, Glyph, PaneToolbarHost, RecordHeader } from "@angee/ui";
import { InboxTranscriptMatches } from "./documents";
import type { DocumentType } from "@angee/gql/console";
import { INBOX_MODELS, type InboxNavigation, type InboxScope } from "./state";
import { useNexusT } from "../i18n";
import { useInboxBreadcrumbs } from "./breadcrumbs";

const Matches = createContext<readonly string[]>([]);

/** The open conversation as the subject: its compact header, then the full Messaging transcript
 *  annotated with result membership around the visible window. */
export function InboxConversation({
  navigation,
  scope,
}: {
  navigation: InboxNavigation;
  scope: InboxScope;
}) {
  const t = useNexusT();
  // The visible window reports membership; the header names the complete conversation.
  const [matches, setMatches] = useState<TranscriptMatches | null>(null);
  const [error, setError] = useState<string | null>(null);
  useInboxBreadcrumbs(navigation, { conversation: { id: navigation.thread,
    label: matches?.thread.conversation_label || t("inbox.conversation") } });
  return (
    <section
      className="flex h-full min-h-0 flex-col"
      aria-label={t("inbox.conversation")}
    >
      <RecordHeader
        density="compact"
        headingLevel={1}
        crumbs={t("inbox.conversation")}
        title={matches?.thread.conversation_label || t("inbox.conversation")}
        meta={matches ? t(matches.count === matches.total ? "inbox.conversationCount" : "inbox.transcriptCount",
          { count: matches.count, total: matches.total }) : t("inbox.loading")}
      />
      <ErrorBanner description={error} />
      {/* The transcript's own controls publish into this pane's toolbar row. */}
      <PaneToolbarHost>
        <ThreadTranscript
          threadId={navigation.thread}
          anchor={navigation.at}
          timezone={scope.coverage.timezone}
          onAnchorChange={(at) => navigation.patch({ at: at || undefined })}
          onOpenReply={navigation.openInContext}
          toolbarActions={matches && matches.count < matches.total ? [
            { icon: "chevron-left", label: t("inbox.previousMatch"), text: true, disabled: !matches.previous,
              onClick: () => navigation.patch({ at: `message:${matches.previous}` }) },
            { icon: "chevron-right", label: t("inbox.nextMatch"), text: true, disabled: !matches.next,
              onClick: () => navigation.patch({ at: `message:${matches.next}` }) },
          ] : undefined}
          renderWindow={(messages, children) => (
            <MatchWindow
              scope={scope}
              navigation={navigation}
              ids={messages.map((message) => message.id)}
              onMatches={setMatches}
              onError={setError}
            >
              {children}
            </MatchWindow>
          )}
          renderMessageActions={(message) => (
            <MessageActions id={message.id} navigation={navigation} />
          )}
          onPreviewFile={(message, part) => {
            if (part.file?.id) navigation.previewFile(part.file.id, message.id);
          }}
        />
      </PaneToolbarHost>
    </section>
  );
}

type TranscriptMatches = NonNullable<DocumentType<typeof InboxTranscriptMatches>["inbox_transcript_matches"]>;

function MatchWindow({
  scope,
  navigation,
  ids,
  children,
  onMatches,
  onError,
}: {
  scope: InboxScope;
  navigation: InboxNavigation;
  ids: string[];
  children: ReactElement;
  onMatches: (matches: TranscriptMatches | null) => void;
  onError: (message: string | null) => void;
}) {
  const query = useAuthoredQuery(
    InboxTranscriptMatches,
    {
      ...scope,
      thread: navigation.thread,
      visible: ids,
      at: navigation.at.startsWith("message:")
        ? navigation.at.slice(8)
        : (ids[0] ?? ""),
    },
    { models: INBOX_MODELS },
  );
  const matches = query.data?.inbox_transcript_matches ?? null;
  const errorMessage = query.error?.message ?? null;
  useEffect(() => { onMatches(matches); }, [matches, onMatches]);
  useEffect(() => { onError(errorMessage); }, [errorMessage, onError]);
  return (
    <Matches.Provider value={matches?.visible ?? []}>
      {children}
    </Matches.Provider>
  );
}

function MessageActions({
  id,
  navigation,
}: {
  id: string;
  navigation: InboxNavigation;
}) {
  const t = useNexusT();
  const matches = useContext(Matches);
  return (
    <>
      {matches.includes(id) ? <Chip tone="brand">{t("inbox.match")}</Chip> : null}
      <Button size="sm" variant="ghost" onClick={() => navigation.read(id)}>
        {t("inbox.details")}
        <Glyph name="chevron-right" />
      </Button>
    </>
  );
}
