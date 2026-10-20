import { createContext, useContext, type ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { ThreadTranscript } from "@angee/messaging";
import { Button, Chip, CommandBand, ErrorBanner, Glyph, PaneToolbarHost, RecordHeader } from "@angee/ui";
import { InboxTranscriptMatches } from "./documents";
import { INBOX_MODELS, type InboxNavigation, type InboxScope } from "./state";
import { useNexusT } from "../i18n";

const Matches = createContext<readonly string[]>([]);

/** The open conversation as the subject: the band (back, match navigation), the record
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
  return (
    <section
      className="flex h-full min-h-0 flex-col"
      aria-label={t("inbox.conversation")}
    >
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
    </section>
  );
}

function MatchWindow({
  scope,
  navigation,
  ids,
  children,
}: {
  scope: InboxScope;
  navigation: InboxNavigation;
  ids: string[];
  children: ReactElement;
}) {
  const t = useNexusT();
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
  const matches = query.data?.inbox_transcript_matches;
  return (
    <Matches.Provider value={matches?.visible ?? []}>
      <CommandBand
        verbs={
          <Button size="sm" variant="ghost" onClick={navigation.results}>
            <Glyph name="chevron-left" />
            {t("inbox.backResults")}
          </Button>
        }
        // The matches walk as the band's pager: the previous and next matching message.
        navigation={matches ? {
          current: matches.count ? undefined : undefined,
          total: matches.count,
          onPrev: matches.previous ? () => navigation.patch({ at: `message:${matches.previous}` }) : undefined,
          onNext: matches.next ? () => navigation.patch({ at: `message:${matches.next}` }) : undefined,
        } : null}
      />
      <RecordHeader
        density="compact"
        headingLevel={1}
        crumbs={t("inbox.conversation")}
        title={matches?.thread.conversation_label || t("inbox.conversation")}
        meta={matches ? t("inbox.transcriptCount", { count: matches.count, total: matches.total }) : t("inbox.loading")}
      />
      <ErrorBanner description={query.error?.message ?? null} />
      {/* The transcript's own controls publish into this pane's toolbar row. */}
      <PaneToolbarHost>{children}</PaneToolbarHost>
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
