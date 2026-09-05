import * as React from "react";
import { type DocumentType } from "@angee/gql/console";
import { senderDisplayName } from "@angee/parties";
import { useAuthoredInfiniteQuery } from "@angee/refine";
import {
  Avatar,
  AvatarFallback,
  Button,
  EmptyState,
  LoadingPanel,
  MessageFeed,
  MessageRow,
  Tag,
  avatarInitials,
} from "@angee/ui";

import { NexusTimeline } from "./documents";
import { useNexusT } from "./i18n";

const PAGE_SIZE = 30;

type TimelinePayload = NonNullable<
  DocumentType<typeof NexusTimeline>["party_message_feed"]
>;
type TimelineMessage = TimelinePayload["messages"][number];
type TimelineDirection = NonNullable<TimelineMessage["direction"]>;

function orderAt(message: TimelineMessage): string {
  return message.sent_at ?? message.created_at ?? "";
}

function directionPresentation(
  direction: TimelineDirection,
): {
  tone: "success" | "info" | "neutral";
  key: "timeline.inbound" | "timeline.outbound" | "timeline.internal";
} {
  if (direction === "OUTBOUND") return { tone: "success", key: "timeline.outbound" };
  if (direction === "INBOUND") return { tone: "info", key: "timeline.inbound" };
  return { tone: "neutral", key: "timeline.internal" };
}

type TimelinePaneProps =
  | { partyId: string; circleId?: never }
  | { circleId: string; partyId?: never };

/** The merged cross-channel feed for one party or the members of a circle subtree. */
export function TimelinePane(props: TimelinePaneProps): React.ReactElement {
  const t = useNexusT();
  const circleId = "circleId" in props ? props.circleId : undefined;
  const partyId = "partyId" in props ? props.partyId : undefined;
  const circle = typeof circleId === "string";
  const scopeId = (circle ? circleId : partyId) ?? "";
  const variables = React.useMemo(
    () => ({
      partyId: scopeId,
      circleId: scopeId,
      circle,
      beforeCursor: null,
      limit: PAGE_SIZE,
      search: "",
    }),
    [circle, scopeId],
  );
  const timeline = useAuthoredInfiniteQuery(
    NexusTimeline,
    variables,
    {
      models: ["messaging.Message", "parties.PartyHandle", "parties.CircleMember"],
      getRows: (data) =>
        (circle ? data.circle_message_feed : data.party_message_feed)?.messages ?? [],
      getRowId: (row) => row.id,
      getPageParam: (_rows, data) => {
        const page = circle ? data.circle_message_feed : data.party_message_feed;
        return page?.has_older && page.older_cursor
          ? { beforeCursor: page.older_cursor }
          : undefined;
      },
    },
  );

  const rows = timeline.rows;
  const firstPage = timeline.data?.pages[0];
  const total = (circle ? firstPage?.circle_message_feed : firstPage?.party_message_feed)?.count
    ?? rows.length;
  const exhausted = !timeline.hasNextPage;

  if (timeline.isFetching && rows.length === 0) return <LoadingPanel />;
  if (timeline.error && rows.length === 0) {
    return <EmptyState icon="triangle-alert" title={timeline.error.message} />;
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        icon="comments"
        title={t(circle ? "timeline.circleEmpty" : "timeline.empty")}
      />
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <p className="pb-2 text-2xs text-fg-subtle">
        {t("timeline.count", { count: total })}
      </p>
      <MessageFeed label={t("timeline.feedLabel")} className="gap-1">
        {rows.map((message) => {
          const author = senderDisplayName(message.sender, "—");
          const title = message.thread?.title?.text ?? "";
          const direction = message.direction
            ? directionPresentation(message.direction)
            : null;
          return (
            <MessageRow
              key={message.id}
              className="rounded-6 px-2 py-2 hover:bg-sheet-2"
              avatar={
                <Avatar size="sm">
                  <AvatarFallback>{avatarInitials(author)}</AvatarFallback>
                </Avatar>
              }
              author={author}
              timestamp={orderAt(message)}
              channel={
                message.platform || direction ? (
                  <>
                    {message.platform ? <Tag tone="neutral">{message.platform}</Tag> : null}
                    {direction ? <Tag tone={direction.tone}>{t(direction.key)}</Tag> : null}
                  </>
                ) : undefined
              }
            >
              <>
                {title ? <div className="truncate text-13 font-medium text-fg">{title}</div> : null}
                {message.preview ? (
                  <div className="line-clamp-2 text-13 text-fg-muted">{message.preview}</div>
                ) : null}
              </>
            </MessageRow>
          );
        })}
      </MessageFeed>
      {exhausted ? null : (
        <Button
          variant="ghost"
          size="sm"
          className="self-center"
          disabled={timeline.isFetching}
          onClick={() => { void timeline.fetchNextPage(); }}
        >
          {t("timeline.loadOlder")}
        </Button>
      )}
    </div>
  );
}
