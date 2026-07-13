import * as React from "react";
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

import { PartyTimeline } from "./documents";
import { useNexusT } from "./i18n";

const PAGE_SIZE = 30;

interface TimelineMessage {
  id: string;
  preview?: string | null;
  platform?: string | null;
  direction?: string | null;
  sent_at?: string | null;
  created_at?: string | null;
  sender?: { id: string; display_name?: string | null; value?: string | null } | null;
  thread?: { id: string; title?: { text?: string | null } | null } | null;
}

function orderAt(message: TimelineMessage): string {
  return message.sent_at ?? message.created_at ?? "";
}

function directionTone(direction: string | undefined | null): "success" | "info" | "neutral" {
  if (direction === "outbound") return "success";
  if (direction === "inbound") return "info";
  return "neutral";
}

/**
 * The merged cross-channel feed exchanged with one party: keyset pages accumulate
 * locally (newest first; "Load older" extends the window), each row carrying its
 * provenance — platform, direction, thread title — so a WhatsApp ping and a mail
 * thread read as what they are.
 */
export function TimelinePane({ partyId }: { partyId: string }): React.ReactElement {
  const t = useNexusT();
  const [before, setBefore] = React.useState<string | undefined>(undefined);
  const [rows, setRows] = React.useState<readonly TimelineMessage[]>([]);
  const { data, fetching, error } = useAuthoredQuery(
    PartyTimeline,
    { partyId, before: before ?? null, limit: PAGE_SIZE, search: "" },
    { models: ["messaging.Message", "parties.PartyHandle"] },
  );

  // Pages accumulate by message id: the query returns one window; older windows
  // merge in as the cursor moves back. A party switch resets the accumulation.
  React.useEffect(() => {
    setRows([]);
    setBefore(undefined);
  }, [partyId]);
  React.useEffect(() => {
    const page = data?.party_timeline?.messages ?? [];
    if (page.length === 0) return;
    setRows((existing) => {
      const byId = new Map(existing.map((row) => [row.id, row]));
      for (const row of page) byId.set(row.id, row as TimelineMessage);
      return [...byId.values()].sort((a, b) =>
        orderAt(a) < orderAt(b) ? 1 : orderAt(a) > orderAt(b) ? -1 : b.id.localeCompare(a.id),
      );
    });
  }, [data]);

  const total = data?.party_timeline?.count ?? 0;
  const oldest = rows.at(-1);
  const exhausted = rows.length >= total;

  if (fetching && rows.length === 0) return <LoadingPanel />;
  if (error && rows.length === 0) {
    return <EmptyState icon="triangle-alert" title={error.message} />;
  }
  if (rows.length === 0) {
    return <EmptyState icon="comments" title={t("timeline.empty")} />;
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
          return (
            <li key={message.id} className="flex gap-2.5 rounded-6 px-2 py-2 hover:bg-sheet-2">
              <Avatar size="sm">
                <AvatarFallback>{avatarInitials(author)}</AvatarFallback>
              </Avatar>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-13 font-medium">{author}</span>
                  {message.platform ? <Tag tone="neutral">{message.platform}</Tag> : null}
                  {message.direction ? (
                    <Tag tone={directionTone(message.direction)}>
                      {t(`timeline.${message.direction}` as "timeline.inbound")}
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
