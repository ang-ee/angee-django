import { createContext, useContext, useEffect, useState, type ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { ThreadTranscript } from "@angee/messaging";
import { Button, Chip, CommandBand, ErrorBanner, Glyph, PaneToolbarHost, RecordHeader } from "@angee/ui";
import { InboxTranscriptMatches } from "./documents";
import type { DocumentType } from "@angee/gql/console";
import { INBOX_MODELS, type InboxNavigation, type InboxScope } from "./state";
import { useNexusT } from "../i18n";
import { useInboxBreadcrumbs } from "./breadcrumbs";

const Matches = createContext<readonly string[]>([]);

/** The open conversation as the subject: the band (match navigation), the record
 *  header (the conversation, how much of it matches), then the full Messaging transcript
 *  annotated with result membership around the visible window. */
export function InboxConversation({
  navigation,
  scope,
}: {
  navigation: InboxNavigation;
  scope: InboxScope;
}) {
  const t = useNexusT();
  // The match window inside the transcript reports its read; the band and header live above it.
  const [matches, setMatches] = useState<TranscriptMatches | null>(null);
  const [error, setError] = useState<string | null>(null);
  useInboxBreadcrumbs(navigation, { conversation: { id: navigation.thread,
    label: matches?.thread.conversation_label || t("inbox.conversation") } });
  return (
    <section
      className="flex h-full min-h-0 flex-col"
      aria-label={t("inbox.conversation")}
    >
      <CommandBand
        // The matches walk as the band's pager: the previous and next matching message.
        navigation={matches ? {
          total: matches.count,
          onPrev: matches.previous ? () => navigation.patch({ at: `message:${matches.previous}` }) : undefined,
          onNext: matches.next ? () => navigation.patch({ at: `message:${matches.next}` }) : undefined,
        } : null}
      />
      <RecordHeader
        headingLevel={1}
        crumbs={t("inbox.conversation")}
        title={matches?.thread.conversation_label || t("inbox.conversation")}
        meta={matches ? t("inbox.transcriptCount", { count: matches.count, total: matches.total }) : t("inbox.loading")}
      />
      <ErrorBanner description={error} />
      {/* The transcript's own controls publish into this pane's toolbar row. */}
      <PaneToolbarHost>
        <ThreadTranscript
          threadId={navigation.thread}
          anchor={navigation.at}
          timezone={scope.coverage.timezone}
          onAnchorChange={(at) => navigation.patch({ at: at || undefined })}
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
        {t("inbox.read")}
        <Glyph name="chevron-right" />
      </Button>
    </>
  );
}
