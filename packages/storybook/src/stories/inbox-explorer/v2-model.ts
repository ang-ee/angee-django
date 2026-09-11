import { ResourceQuery } from "@angee/metadata";

import {
  ME, NOW, accountById, circles, messages, senderName, senders,
  threadById, threadLabel, platformLabel,
  type Message,
} from "./fixtures";

/** Domain projections of the shared, bounded fixture. Collection mechanics live in RowsListView. */
export type Coverage = { platform: string; account: string; kind: string; period: string };
export const allCoverage: Coverage = { platform: "all", account: "all", kind: "all", period: "all" };
export type RelatedTarget = { kind: "file" | "fragment"; id: string; anchor: string };

export function coveredMessages(coverage: Coverage): readonly Message[] {
  const since = coverage.period === "all" ? 0 : NOW - Number(coverage.period) * 86_400_000;
  return messages.filter((message) => {
    const account = accountById.get(message.accountId)!;
    const thread = threadById.get(message.threadId)!;
    return (coverage.platform === "all" || account.platform === coverage.platform)
      && (coverage.account === "all" || account.id === coverage.account)
      && (coverage.kind === "all" || thread.kind === coverage.kind)
      && Date.parse(message.sentAt) >= since;
  });
}

function circleMembers(id: string): readonly string[] {
  return [...(circles.find((circle) => circle.id === id)?.members ?? []),
    ...circles.filter((circle) => circle.parent === id).flatMap((circle) => circleMembers(circle.id))];
}

export function scopedMessages(source: readonly Message[], sender: string | null, circle: string): readonly Message[] {
  const members = sender ? [sender] : circle !== "all" ? circleMembers(circle) : null;
  if (!members) return source;
  return source.filter((message) => message.direction === "inbound"
    ? members.includes(message.senderId)
    : message.to?.some((id) => members.includes(id)));
}

export function senderRows(source: readonly Message[]) {
  return senders.flatMap((sender) => {
    const activity = source.filter((message) => message.senderId === sender.id);
    if (!activity.length) return [];
    return [{ ...sender, title: `${sender.name} ${sender.address}`, count: activity.length,
      latest: activity.reduce((latest, message) => message.sentAt > latest ? message.sentAt : latest, "") }];
  });
}
export type SenderRow = ReturnType<typeof senderRows>[number];

export function messageRows(source: readonly Message[]) {
  return source.map((message) => ({
    id: message.id,
    message,
    // The shared text-search owner searches declared columns. Keep attachment names searchable too.
    title: [message.subject, message.body, senderName(message.senderId),
      ...message.attachments?.map((file) => file.name) ?? []].filter(Boolean).join(" "),
    sentAt: message.sentAt,
    platform: platformLabel[accountById.get(message.accountId)!.platform],
    direction: message.direction,
    hasFiles: Boolean(message.attachments?.length),
    starred: Boolean(message.starred),
    thread: { id: message.threadId, label: threadLabel(threadById.get(message.threadId)!) },
  }));
}
export type InboxRow = ReturnType<typeof messageRows>[number];

export const senderQuery = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, count: { scalar: "Int" }, latest: { scalar: "DateTime" },
  link: { scalar: "String" },
} });
export const messageQuery = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, sentAt: { scalar: "DateTime" }, platform: { scalar: "String" },
  direction: { scalar: "String" }, hasFiles: { scalar: "Boolean" }, starred: { scalar: "Boolean" },
  thread: { kind: "relation", identityPath: "thread.id", labelPath: "thread.label" },
} });

export function relatedMessages(target: RelatedTarget): readonly Message[] {
  return messages.filter((message) => target.kind === "file"
    ? message.attachments?.some((file) => file.fileId === target.id)
    : message.fragments?.includes(target.id));
}

export const isMine = (message: Message) => message.senderId === ME;
