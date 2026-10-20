import { ResourceQuery, type QueryFilter } from "@angee/metadata";
import { NOW, ME, accounts, accountById, circles, messages, senders, senderName,
  snippet, threadById, threadLabel, type Message } from "./fixtures";
import { allCoverage, scopedMessages, type Coverage } from "./v2-model";

// A bounded story projection, standing in for the documented Nexus read models.
// Existing fixtures and both earlier mockups remain untouched.
export const sampleMessages: readonly Message[] = [...messages, {
  id: "msg_standalone", threadId: "", senderId: "unknown", direction: "inbound",
  accountId: "acc_mail", sentAt: new Date(NOW - 36 * 3_600_000).toISOString(),
  subject: "Receipt for your archive", body: "This imported message has no conversation or resolved sender. It is still available in the inbox.",
}];

export const sampleQuery = ResourceQuery.forRows({ fields: {
  title: { scalar: "String" }, latest: { scalar: "DateTime" },
  hasFiles: { scalar: "Boolean" }, starred: { scalar: "Boolean" },
  direction: { scalar: "String" },
} });

export function searchable(message: Message, quoted = false) {
  return [message.subject, message.body, ...(message.attachments ?? []).map(file => file.name),
    ...(quoted ? [message.quoted, message.signature] : [])].filter(Boolean).join(" ");
}

export function messageProjection(message: Message, quoted = false) {
  return { id: message.id, message, title: searchable(message, quoted), latest: message.sentAt,
    hasFiles: Boolean(message.attachments?.length), starred: Boolean(message.starred), direction: message.direction };
}
export type MessageProjection = ReturnType<typeof messageProjection>;

export function covered(coverage: Coverage = allCoverage) {
  const since = coverage.period === "all" ? 0 : NOW - Number(coverage.period) * 86_400_000;
  return sampleMessages.filter(message => {
    const account = accountById.get(message.accountId)!;
    const kind = threadById.get(message.threadId)?.kind ?? "mail";
    return (coverage.platform === "all" || account.platform === coverage.platform)
      && (coverage.account === "all" || account.id === coverage.account)
      && (coverage.kind === "all" || kind === coverage.kind) && Date.parse(message.sentAt) >= since;
  });
}

export function matchedMessages(coverage: Coverage, sender: string, circle: string, filter: QueryFilter, quoted: boolean) {
  return scopedMessages(covered(coverage), sender || null, circle)
    .filter(message => sampleQuery.matches(messageProjection(message, quoted), filter));
}

export function activityRows(coverage: Coverage, includeSent: boolean) {
  const source = covered(coverage);
  return senders.flatMap(sender => {
    const activity = source.filter(message => message.direction === "inbound" ? message.senderId === sender.id
      : includeSent && message.to?.includes(sender.id)).sort((a, b) => b.sentAt.localeCompare(a.sentAt));
    const latest = activity[0];
    return latest ? [{ ...sender, title: `${sender.name} ${sender.address}`, count: activity.length,
      latest: latest.sentAt, preview: `${latest.direction === "outbound" ? "You: " : ""}${snippet(latest)}`,
      context: threadById.get(latest.threadId)?.kind === "group" ? threadById.get(latest.threadId)?.title : undefined }] : [];
  });
}
export type ActivityRow = ReturnType<typeof activityRows>[number];

export function conversationRows(matches: readonly Message[], quoted: boolean) {
  const groups = new Map<string, Message[]>();
  for (const message of matches) {
    const key = message.threadId || message.id;
    groups.set(key, [...(groups.get(key) ?? []), message]);
  }
  return [...groups].map(([id, hits]) => {
    hits.sort((a, b) => b.sentAt.localeCompare(a.sentAt));
    const newest = hits[0]!;
    const thread = threadById.get(newest.threadId);
    const full = thread ? sampleMessages.filter(message => message.threadId === thread.id) : hits;
    return { ...messageProjection(newest, quoted), id, threadId: thread?.id,
      label: thread ? threadLabel(thread) : "Standalone message", kind: thread?.kind ?? "mail",
      title: hits.map(message => searchable(message, quoted)).join(" "), hits,
      total: full.length, preview: hits.slice(0, 3),
      accounts: [...new Set(hits.map(message => accountById.get(message.accountId)!.label))],
    };
  });
}
export type ConversationRow = ReturnType<typeof conversationRows>[number];

export function targetUses(target: string) {
  const [kind, id] = target.split(":");
  return sampleMessages.filter(message => kind === "file" ? message.attachments?.some(file => file.fileId === id)
    : kind === "fragment" && message.fragments?.includes(id!));
}

export const scopeName = (sender: string, circle: string) => sender ? senderName(sender)
  : circles.find(item => item.id === circle)?.name ?? "Everyone";
export const messageConversation = (message: Message) => {
  const thread = threadById.get(message.threadId);
  return thread ? threadLabel(thread) : "Standalone message";
};
export const accountOptions = [{ value: "all", label: "All accounts" }, ...accounts.map(item => ({ value: item.id, label: item.label }))];
export { allCoverage, ME };
