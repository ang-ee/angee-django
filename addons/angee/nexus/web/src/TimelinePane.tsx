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
  RelativeTime,
  Tag,
  avatarInitials,
} from "@angee/ui";

import { NexusTimeline } from "./documents";
import { useNexusT } from "./i18n";

const PAGE_SIZE = 30;

type TimelinePayload = NonNullable<
  DocumentType<typeof NexusTimeline>["party_timeline"]
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
      before: null,
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
        (circle ? data.circle_timeline : data.party_timeline)?.messages ?? [],
      getRowId: (row) => row.id,
      getPageParam: (pageRows) => {
        const oldest = pageRows.length >= PAGE_SIZE ? pageRows.at(-1) : undefined;
        return oldest ? { before: oldest.id } : undefined;
      },
    },
  );

  const rows = React.useMemo(
    () =>
      [...timeline.rows].sort((a, b) =>
        orderAt(a) < orderAt(b) ? 1 : orderAt(a) > orderAt(b) ? -1 : b.id.localeCompare(a.id),
      ),
    [timeline.rows],
  );
  const firstPage = timeline.pages[0];
  const total = (circle ? firstPage?.circle_timeline : firstPage?.party_timeline)?.count
    ?? rows.length;
  const exhausted = !timeline.hasMore || rows.length >= total;

  if (timeline.fetching && rows.length === 0) return <LoadingPanel />;
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
      <ul className="flex flex-col gap-1">
        {rows.map((message) => {
          const author = senderDisplayName(message.sender, "—");
          const title = message.thread?.title?.text ?? "";
          const direction = message.direction
            ? directionPresentation(message.direction)
            : null;
          return (
            <li key={message.id} className="flex gap-2.5 rounded-6 px-2 py-2 hover:bg-sheet-2">
              <Avatar size="sm">
                <AvatarFallback>{avatarInitials(author)}</AvatarFallback>
              </Avatar>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-13 font-medium">{author}</span>
                  {message.platform ? <Tag tone="neutral">{message.platform}</Tag> : null}
                  {direction ? (
                    <Tag tone={direction.tone}>
                      {t(direction.key)}
                    </Tag>
                  ) : null}
                  <RelativeTime value={orderAt(message)} className="text-2xs text-fg-subtle" />
                </div>
                {title ? <div className="truncate text-13 font-medium text-fg">{title}</div> : null}
                {message.preview ? (
                  <div className="line-clamp-2 text-13 text-fg-muted">{message.preview}</div>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {exhausted ? null : (
        <Button
          variant="ghost"
          size="sm"
          className="self-center"
          disabled={timeline.fetching}
          onClick={timeline.fetchOlder}
        >
          {t("timeline.loadOlder")}
        </Button>
      )}
    </div>
  );
}
