import * as React from "react";
import { type DocumentType } from "@angee/gql/console";
import { useAuthoredQuery } from "@angee/refine";
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
  const [before, setBefore] = React.useState<string | undefined>(undefined);
  const [rows, setRows] = React.useState<readonly TimelineMessage[]>([]);
  const { data, fetching, error } = useAuthoredQuery(
    NexusTimeline,
    {
      partyId: scopeId,
      circleId: scopeId,
      circle,
      before: before ?? null,
      limit: PAGE_SIZE,
      search: "",
    },
    { models: ["messaging.Message", "parties.PartyHandle", "parties.CircleMember"] },
  );

  // Pages accumulate by message id: the query returns one window; older windows
  // merge in as the cursor moves back. A party switch resets the accumulation.
  React.useEffect(() => {
    setRows([]);
    setBefore(undefined);
  }, [scopeId]);
  React.useEffect(() => {
    const page = (circle ? data?.circle_timeline : data?.party_timeline)?.messages ?? [];
    if (page.length === 0) return;
    setRows((existing) => {
      const byId = new Map(existing.map((row) => [row.id, row]));
      for (const row of page) byId.set(row.id, row as TimelineMessage);
      return [...byId.values()].sort((a, b) =>
        orderAt(a) < orderAt(b) ? 1 : orderAt(a) > orderAt(b) ? -1 : b.id.localeCompare(a.id),
      );
    });
  }, [circle, data]);

  const total = (circle ? data?.circle_timeline : data?.party_timeline)?.count ?? 0;
  const oldest = rows.at(-1);
  const exhausted = rows.length >= total;

  if (fetching && rows.length === 0) return <LoadingPanel />;
  if (error && rows.length === 0) {
    return <EmptyState icon="triangle-alert" title={error.message} />;
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
          const author =
            message.sender?.display_name || message.sender?.value || "—";
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
          disabled={fetching}
          onClick={() => oldest && setBefore(oldest.id)}
        >
          {t("timeline.loadOlder")}
        </Button>
      )}
    </div>
  );
}
