// Illustrative data for the Inbox Explorer design study (addons/angee/nexus/docs/ux.md).
// Nothing here is a production contract: ids, names and counts exist only so the
// mockup can demonstrate every interaction the spec describes.

export const NOW = Date.now();
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
export const ago = (ms: number): string => new Date(NOW - ms).toISOString();

export type Platform = "whatsapp" | "telegram" | "email" | "signal";
export type Kind = "direct" | "group" | "mail";
export type LinkState = "confirmed" | "suggested" | "unlinked";

export interface Account {
  id: string;
  label: string;
  platform: Platform;
  health?: "ok" | "stale";
}

export interface Circle {
  id: string;
  name: string;
  parent: string | null;
  members: readonly string[];
}

export interface Sender {
  id: string;
  name: string;
  link: LinkState;
  /** Raw address shown for handle rows and in Details. */
  address: string;
  platforms: readonly Platform[];
  fading?: boolean;
  /** Party id a suggested handle would link to. */
  candidate?: string;
  firstSeen: string;
}

export interface Thread {
  id: string;
  kind: Kind;
  title: string | null;
  accounts: readonly string[];
  participants: readonly string[];
  total: number;
}

export interface Attachment {
  fileId: string;
  name: string;
  size: string;
  mime: string;
  preview?: "image" | "video";
}

export interface Message {
  id: string;
  threadId: string;
  senderId: string;
  direction: "inbound" | "outbound";
  accountId: string;
  sentAt: string;
  subject?: string;
  body: string;
  quoted?: string;
  signature?: string;
  attachments?: readonly Attachment[];
  /** Shared-text fragment ids carried by this message. */
  fragments?: readonly string[];
  parentId?: string;
  starred?: boolean;
  edited?: boolean;
  reactions?: readonly { reaction: string; count: number }[];
  /** Explicitly addressed recipients of an outbound message. */
  to?: readonly string[];
}

export const ME = "me";

export const accounts: readonly Account[] = [
  { id: "acc_wa", label: "Alexis WhatsApp", platform: "whatsapp" },
  { id: "acc_tg", label: "Alexis Telegram", platform: "telegram" },
  { id: "acc_mail", label: "alexis@apexive.com", platform: "email", health: "stale" },
  { id: "acc_sig", label: "Signal", platform: "signal" },
];

export const platformLabel: Record<Platform, string> = {
  whatsapp: "WhatsApp",
  telegram: "Telegram",
  email: "Email",
  signal: "Signal",
};

export const circles: readonly Circle[] = [
  { id: "cir_family", name: "Family", parent: null, members: ["pty_anna", "pty_marek"] },
  { id: "cir_close", name: "Close family", parent: "cir_family", members: ["pty_anna"] },
  { id: "cir_work", name: "Work", parent: null, members: ["pty_sofia"] },
];

export const senders: readonly Sender[] = [
  { id: "pty_anna", name: "Anna Kowalski", link: "confirmed", address: "+48 601 220 118", platforms: ["whatsapp", "email"], firstSeen: ago(400 * DAY) },
  { id: "hdl_j", name: "J", link: "unlinked", address: "+1 424 479 8217", platforms: ["whatsapp"], firstSeen: ago(60 * DAY) },
  { id: "pty_marek", name: "Marek Nowak", link: "confirmed", address: "+48 500 111 222", platforms: ["whatsapp", "telegram"], firstSeen: ago(900 * DAY) },
  { id: "hdl_toptal", name: "Toptal", link: "unlinked", address: "team@toptal.com", platforms: ["email"], firstSeen: ago(7 * HOUR) },
  { id: "hdl_oceans", name: "Oceans Property Management", link: "unlinked", address: "+1 305 555 0143", platforms: ["whatsapp"], firstSeen: ago(200 * DAY) },
  { id: "hdl_sofia", name: "sofia.m", link: "suggested", address: "@sofia_m", platforms: ["telegram"], candidate: "pty_sofia", firstSeen: ago(30 * DAY) },
  { id: "pty_sofia", name: "Sofia Marin", link: "confirmed", address: "sofia@northstar.studio", platforms: ["email"], firstSeen: ago(120 * DAY) },
  { id: "hdl_kirill", name: "Kirill Shikhalev", link: "unlinked", address: "@kshikhalev", platforms: ["telegram"], firstSeen: ago(300 * DAY) },
  { id: "pty_palmas", name: "Palmas United", link: "confirmed", address: "signal group", platforms: ["signal"], fading: true, firstSeen: ago(500 * DAY) },
];

export const threads: readonly Thread[] = [
  { id: "thr_anna", kind: "direct", title: null, accounts: ["acc_wa"], participants: ["pty_anna", ME], total: 42 },
  { id: "thr_family", kind: "group", title: "Family group", accounts: ["acc_wa"], participants: ["pty_anna", "pty_marek", ME, "hdl_oceans"], total: 1383 },
  { id: "thr_invoice", kind: "mail", title: "Invoice August 2026", accounts: ["acc_mail"], participants: ["pty_anna", ME], total: 3 },
  { id: "thr_j", kind: "direct", title: null, accounts: ["acc_wa"], participants: ["hdl_j", ME], total: 14 },
  { id: "thr_odoo", kind: "group", title: "Odoo Developers", accounts: ["acc_tg"], participants: ["hdl_kirill", ME], total: 381 },
  { id: "thr_toptal", kind: "mail", title: "Getting started with Toptal", accounts: ["acc_mail"], participants: ["hdl_toptal", ME], total: 3 },
  { id: "thr_marek", kind: "direct", title: null, accounts: ["acc_tg", "acc_wa"], participants: ["pty_marek", ME], total: 120 },
  { id: "thr_sofia_tg", kind: "direct", title: null, accounts: ["acc_tg"], participants: ["hdl_sofia", ME], total: 8 },
  { id: "thr_sofia_mail", kind: "mail", title: "Customer portal proposal", accounts: ["acc_mail"], participants: ["pty_sofia", ME], total: 6 },
  { id: "thr_oceans", kind: "direct", title: null, accounts: ["acc_wa"], participants: ["hdl_oceans", ME], total: 5 },
  { id: "thr_palmas", kind: "group", title: "Palmas United", accounts: ["acc_sig"], participants: ["pty_palmas", ME], total: 339 },
];

const invoice: Attachment = { fileId: "fil_invoice", name: "invoice-aug.pdf", size: "184 KB", mime: "application/pdf" };
const invoiceRenamed: Attachment = { fileId: "fil_invoice", name: "Kowalski_invoice_08.pdf", size: "184 KB", mime: "application/pdf" };
const video: Attachment = { fileId: "fil_video", name: "attachment.bin", size: "3.6 MB", mime: "video/mp4", preview: "video" };
const photo: Attachment = { fileId: "fil_photo", name: "IMG_2231.jpg", size: "1.1 MB", mime: "image/jpeg", preview: "image" };
const logo: Attachment = { fileId: "fil_logo", name: "logo.png", size: "12 KB", mime: "image/png", preview: "image" };

export const fragments: Readonly<Record<string, string>> = {
  frg_tonight: "Sure, sending tonight — the August one includes the extra day.",
  frg_hundreds: "hundreds of articles on the topic. Give me one good reason not to avoid pure long positions",
};

export const messages: readonly Message[] = [
  // J — unlinked WhatsApp handle, media-only, newest
  { id: "msg_j3", threadId: "thr_j", senderId: "hdl_j", direction: "inbound", accountId: "acc_wa", sentAt: ago(10 * MIN), body: "", attachments: [video] },
  { id: "msg_j2", threadId: "thr_j", senderId: "hdl_j", direction: "inbound", accountId: "acc_wa", sentAt: ago(21 * HOUR), body: "https://youtu.be/NufJ7g63KSY hundreds of articles on the topic. Give me one good reason not to avoid pure long positions", fragments: ["frg_hundreds"] },
  { id: "msg_j1", threadId: "thr_j", senderId: "hdl_j", direction: "inbound", accountId: "acc_wa", sentAt: ago(24 * HOUR), body: "https://suno.com/s/lWA6lJD3HljjnaqV full song" },
  // Anna — direct WhatsApp
  { id: "msg_anna_d3", threadId: "thr_anna", senderId: "pty_anna", direction: "inbound", accountId: "acc_wa", sentAt: ago(2 * HOUR), body: "Can you send the invoice for August? The accountant needs it before Friday.", starred: true },
  { id: "msg_me_d2", threadId: "thr_anna", senderId: ME, direction: "outbound", accountId: "acc_wa", sentAt: ago(3 * HOUR), body: "Sure, sending tonight — the August one includes the extra day.", fragments: ["frg_tonight"], to: ["pty_anna"] },
  { id: "msg_anna_d1", threadId: "thr_anna", senderId: "pty_anna", direction: "inbound", accountId: "acc_wa", sentAt: ago(26 * HOUR), body: "Here is the signed one back.", attachments: [invoiceRenamed], parentId: "msg_me_d2" },
  // Family group
  { id: "msg_fam4", threadId: "thr_family", senderId: "pty_marek", direction: "inbound", accountId: "acc_wa", sentAt: ago(5 * HOUR), body: "🔥🔥🔥🔥🔥", attachments: [photo], reactions: [{ reaction: "❤️", count: 2 }] },
  { id: "msg_fam3", threadId: "thr_family", senderId: "pty_anna", direction: "inbound", accountId: "acc_wa", sentAt: ago(1 * DAY + 2 * HOUR), body: "Forwarding the invoice so everyone has it.", attachments: [invoice] },
  { id: "msg_fam2", threadId: "thr_family", senderId: "hdl_oceans", direction: "inbound", accountId: "acc_wa", sentAt: ago(1 * DAY + 6 * HOUR), body: "Never forget. Rest in peace to so much of civility and mutual trust." },
  { id: "msg_fam1", threadId: "thr_family", senderId: ME, direction: "outbound", accountId: "acc_wa", sentAt: ago(2 * DAY), body: "Dinner Saturday at ours?", to: ["pty_anna", "pty_marek"] },
  // Invoice mail thread
  { id: "msg_mail3", threadId: "thr_invoice", senderId: "pty_anna", direction: "inbound", accountId: "acc_mail", sentAt: ago(3 * DAY), subject: "Re: Invoice August 2026", body: "Thanks! Attached the countersigned copy for your records.", quoted: "> Sure, sending tonight — the August one includes the extra day.", signature: "Anna Kowalski\nKowalski Consulting · +48 601 220 118", attachments: [invoice, logo], fragments: ["frg_tonight"] },
  { id: "msg_mail2", threadId: "thr_invoice", senderId: ME, direction: "outbound", accountId: "acc_mail", sentAt: ago(3 * DAY + 4 * HOUR), subject: "Re: Invoice August 2026", body: "Attached. Let me know if the PO number is right.", attachments: [invoice, logo], to: ["pty_anna"] },
  { id: "msg_mail1", threadId: "thr_invoice", senderId: "pty_anna", direction: "inbound", accountId: "acc_mail", sentAt: ago(4 * DAY), subject: "Invoice August 2026", body: "Hi Alexis, could you send over the August invoice when you have a minute?", signature: "Anna Kowalski\nKowalski Consulting" },
  // Toptal mail
  { id: "msg_toptal", threadId: "thr_toptal", senderId: "hdl_toptal", direction: "inbound", accountId: "acc_mail", sentAt: ago(7 * HOUR), subject: "Getting started with Toptal", body: "Welcome aboard. Your onboarding call is scheduled for Monday; the attached guide covers the first week.", attachments: [{ fileId: "fil_guide", name: "onboarding-guide.pdf", size: "2.1 MB", mime: "application/pdf" }] },
  // Odoo group
  { id: "msg_odoo2", threadId: "thr_odoo", senderId: "hdl_kirill", direction: "inbound", accountId: "acc_tg", sentAt: ago(17 * MIN), body: "Python 3.14 free-threading works with the ORM if you pin psycopg 3.3." },
  { id: "msg_odoo1", threadId: "thr_odoo", senderId: "hdl_kirill", direction: "inbound", accountId: "acc_tg", sentAt: ago(1 * DAY + 3 * HOUR), body: "Anyone tried the new invoice matching in 19?" },
  // Marek direct (two accounts)
  { id: "msg_marek2", threadId: "thr_marek", senderId: "pty_marek", direction: "inbound", accountId: "acc_tg", sentAt: ago(2 * DAY + 1 * HOUR), body: "Got the photos, thanks. Same time next week?" },
  { id: "msg_marek1", threadId: "thr_marek", senderId: ME, direction: "outbound", accountId: "acc_wa", sentAt: ago(2 * DAY + 3 * HOUR), body: "Sent you the climbing photos on Telegram.", to: ["pty_marek"] },
  // Sofia (suggested handle on Telegram, confirmed party on mail)
  { id: "msg_sofia_tg", threadId: "thr_sofia_tg", senderId: "hdl_sofia", direction: "inbound", accountId: "acc_tg", sentAt: ago(1 * DAY + 5 * HOUR), body: "Is the portal proposal still on for Thursday?" },
  { id: "msg_sofia_mail", threadId: "thr_sofia_mail", senderId: "pty_sofia", direction: "inbound", accountId: "acc_mail", sentAt: ago(6 * DAY), subject: "Customer portal proposal", body: "Sharing the revised proposal with the milestone plan we discussed.", attachments: [{ fileId: "fil_proposal", name: "portal-proposal-v3.pdf", size: "980 KB", mime: "application/pdf" }] },
  // Oceans direct
  { id: "msg_oceans", threadId: "thr_oceans", senderId: "hdl_oceans", direction: "inbound", accountId: "acc_wa", sentAt: ago(18 * HOUR), body: "Reminder: pool maintenance Thursday 9–11.", attachments: [{ fileId: "fil_notice", name: "attachment.bin", size: "58 KB", mime: "image/jpeg", preview: "image" }] },
  // Palmas (fading)
  { id: "msg_palmas", threadId: "thr_palmas", senderId: "pty_palmas", direction: "inbound", accountId: "acc_sig", sentAt: ago(40 * DAY), body: "Training moved to 7pm this week." },
];

export const senderById = new Map(senders.map((sender) => [sender.id, sender]));
export const threadById = new Map(threads.map((thread) => [thread.id, thread]));
export const accountById = new Map(accounts.map((account) => [account.id, account]));

export function senderName(id: string): string {
  if (id === ME) return "You";
  return senderById.get(id)?.name ?? "Unknown sender";
}

/** Messages carrying the same storage file id (the R1 "same file" contract). */
export function messagesWithFile(fileId: string): Message[] {
  return messages.filter((message) => message.attachments?.some((attachment) => attachment.fileId === fileId));
}

/** Messages carrying the exact shared fragment. */
export function messagesWithFragment(fragmentId: string): Message[] {
  return messages.filter((message) => message.fragments?.includes(fragmentId));
}

/** Untitled threads are labelled by known participants, selected person first, self omitted. */
export function threadLabel(thread: Thread, preferred?: string): string {
  if (thread.title) return thread.title;
  const others = thread.participants.filter((participant) => participant !== ME);
  const ordered = preferred && others.includes(preferred)
    ? [preferred, ...others.filter((participant) => participant !== preferred)]
    : others;
  if (ordered.length === 0) return "Untitled conversation";
  const shown = ordered.slice(0, 2).map(senderName);
  const rest = ordered.length - shown.length;
  return rest > 0 ? `${shown.join(", ")} +${rest}` : shown.join(", ");
}

export function snippet(message: Message): string {
  if (message.subject && message.direction === "inbound") return message.subject;
  if (message.body) return message.body;
  const attachment = message.attachments?.[0];
  if (!attachment) return "";
  if (attachment.mime.startsWith("video/")) return "Video";
  if (attachment.mime.startsWith("image/")) return "Image";
  return attachment.name;
}
