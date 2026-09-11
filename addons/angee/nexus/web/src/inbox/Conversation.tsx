import { createContext, useContext, type ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";
import { ThreadTranscript } from "@angee/messaging";
import { Alert, Button, Glyph, PageHeader, Tag } from "@angee/ui";
import { InboxTranscriptMatches } from "./documents";
import { INBOX_MODELS, type InboxNavigation, type InboxScope } from "./state";
import { useNexusT } from "../i18n";

const Matches = createContext<readonly string[]>([]);

/** Full Messaging transcript, with a bounded result-membership annotation around the visible window. */
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
      <PageHeader
        density="compact"
        title={t("inbox.conversation")}
        actions={
          <Button size="sm" variant="secondary" onClick={navigation.results}>
            <Glyph name="chevron-left" />
            {t("inbox.backResults")}
          </Button>
        }
      />
      <div className="min-h-0 flex-1 overflow-auto">
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
        />
      </div>
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
      <div className="space-y-2 border-b border-border-subtle p-3">
        <div className="font-medium text-13">
          {matches?.thread.title?.text || t("inbox.conversation")}
        </div>
        <div className="flex items-center gap-2 text-2xs text-fg-muted">
          <span className="mr-auto">
            {matches
              ? t("inbox.transcriptCount", {
                  count: matches.count,
                  total: matches.total,
                })
              : t("inbox.loading")}
          </span>
          <Button
            size="sm"
            variant="ghost"
            disabled={!matches?.previous}
            onClick={() =>
              navigation.patch({ at: `message:${matches!.previous}` })
            }
          >
            <Glyph name="chevron-left" />
            {t("inbox.previousMatch")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={!matches?.next}
            onClick={() => navigation.patch({ at: `message:${matches!.next}` })}
          >
            {t("inbox.nextMatch")}
            <Glyph name="chevron-right" />
          </Button>
        </div>
        {query.error ? (
          <Alert tone="danger">{query.error.message}</Alert>
        ) : null}
      </div>
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
    <div className="flex items-center gap-2">
      {matches.includes(id) ? <Tag tone="brand">{t("inbox.match")}</Tag> : null}
      <Button size="sm" variant="ghost" onClick={() => navigation.read(id)}>
        {t("inbox.read")}
        <Glyph name="chevron-right" />
      </Button>
    </div>
  );
}
