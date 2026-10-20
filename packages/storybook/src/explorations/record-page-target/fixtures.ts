import type { DataResourceLinesMetadata } from "@angee/metadata";
import type { PaneRowProps } from "@angee/ui";

/** AP audit identities and values; the waiting state is the 2026-10-20 target ruling.
 * Audit 01 is a later, posted snapshot. Surrounding activity is illustrative.
 * Dates are explicit so screenshots do not depend on the viewer's clock.
 */
export const bill = {
  number: "SYN-UBL-9311",
  supplier: "Cloudflare, Inc.",
  company: "Apexive Solutions LLC",
  amount: "€307.14",
  invoiceDate: "Sep 26, 2026",
  dueDate: "Oct 15, 2026",
  currency: "EUR — Euro",
  journal: "Vendor Bills",
  payment: "Not paid",
  file: "v6-cloudflare-9311-notax.xml",
  size: "5.8 KB",
  agreement: "Cloudflare, Inc. agreement",
  printedBuyer: "Synthetic Buyer BV",
  printedAddress: ["Musterweg 12 · Hof 3", "10115 Berlin · Germany"],
  buyerReference: "PO-7781",
  tax: "€0.00",
  format: "UBL 2.1",
  lines: [
    { id: "consulting", label: "Remote consulting, September", quantity: 2, unit: 100, subtotal: 200, currency: "EUR", position: 0 },
    { id: "manual", label: "Printed manual", quantity: 3, unit: 35.713333, subtotal: 107.14, currency: "EUR", position: 1 },
  ],
};

export const billRows = [
  { id: bill.number, label: bill.number, supplier: bill.supplier, amount: bill.amount, status: "waiting" },
  { id: "SYN-UBL-9507", label: "SYN-UBL-9507", supplier: bill.supplier, amount: "€307.14", status: "draft" },
  { id: "INV-2211", label: "INV-2211", supplier: "Hetzner Online", amount: "€48.00", status: "confirmed" },
  { id: "INV-0931", label: "INV-0931", supplier: "Notion Labs", amount: "$96.00", status: "confirmed" },
  { id: "M/5/5/R1", label: "M/5/5/R1", supplier: "Piloto 151", amount: "$75.00", status: "confirmed" },
] as const;

export type PageKind = "bill" | "file" | "knowledge" | "nexus";
export type AsideTab = "timeline" | "discussion" | "tasks" | "related";
export type TreeRow = { id: string; parent: string | null; title: string; icon: string };
export const trees: Record<"file" | "knowledge", TreeRow[]> = {
  file: [
    { id: "inbox", parent: null, title: "Inbox", icon: "archive" },
    { id: "year", parent: null, title: "2026", icon: "folder" },
    { id: "october", parent: "year", title: "October", icon: "folder" },
    { id: "file", parent: "october", title: bill.file, icon: "file" },
    { id: "hetzner", parent: "october", title: "hetzner-2211.pdf", icon: "file" },
    { id: "contracts", parent: null, title: "Contracts", icon: "folder" },
  ],
  knowledge: [
    { id: "policies", parent: null, title: "Policies", icon: "folder" },
    { id: "contracts", parent: null, title: "Supplier contracts", icon: "folder" },
    { id: "knowledge", parent: "contracts", title: bill.agreement, icon: "notes" },
    { id: "hetzner", parent: "contracts", title: "Hetzner agreement", icon: "notes" },
    { id: "runbooks", parent: null, title: "Runbooks", icon: "folder" },
    { id: "close", parent: "runbooks", title: "October close", icon: "notes" },
  ],
};

export const access = [
  { name: "admin", initials: "AD" },
  { name: "Walker Member", initials: "WM" },
  { name: "AP team", initials: "AP" },
];

export type Activity = {
  id: string;
  day: string;
  at: string;
  time: string;
  kind: string;
  icon: string;
  title: string;
  detail?: string;
  actor: string;
  folded?: string;
  needsMe?: boolean;
  tone?: PaneRowProps["tone"];
};
export const billTimeline: Activity[] = [
  { id: "run", day: "Oct 20, 2026", at: "2026-10-20T09:41:00-04:00", time: "09:41 AM", kind: "Runs", icon: "activity", title: "Bill import", detail: "Waiting · Review contract deviations", actor: "admin", folded: "3 changes · 5 decisions", needsMe: true, tone: "warning" },
  { id: "access", day: "Oct 20, 2026", at: "2026-10-20T09:12:00-04:00", time: "09:12 AM", kind: "Access", icon: "lock", title: "Shared with Walker Member", detail: "Read access", actor: "admin" },
  { id: "comment", day: "Oct 19, 2026", at: "2026-10-19T17:02:00-04:00", time: "05:02 PM", kind: "Comments", icon: "comments", title: "Rate on line 1 is above the agreement", detail: "Asked Cloudflare for a credit note", actor: "Walker Member" },
  { id: "conversation", day: "Oct 19, 2026", at: "2026-10-19T16:40:00-04:00", time: "04:40 PM", kind: "Conversations", icon: "comments", title: `Re: Invoice ${bill.number}`, detail: "3 messages", actor: "AP Inbox" },
  { id: "task", day: "Oct 19, 2026", at: "2026-10-19T16:30:00-04:00", time: "04:30 PM", kind: "Tasks", icon: "check", title: "Verify the printed bank account", detail: "Due Oct 21", actor: "Walker Member" },
  { id: "file", day: "Oct 19, 2026", at: "2026-10-19T09:35:00-04:00", time: "09:35 AM", kind: "Files", icon: "file", title: bill.file, detail: "Attached", actor: "admin" },
  { id: "source", day: "Oct 17, 2026", at: "2026-10-17T08:00:00-04:00", time: "08:00 AM", kind: "Sources", icon: "link", title: "Pulled from Odoo", detail: "Vendor bill", actor: "us.apexive.com" },
];
export const timelines: Record<PageKind, Activity[]> = {
  bill: billTimeline,
  file: [
    billTimeline[0]!,
    { id: "upload", day: "Oct 19, 2026", at: "2026-10-19T09:35:00-04:00", time: "09:35 AM", kind: "Changes", icon: "file", title: "Uploaded", detail: "Structured e-invoice · UBL 2.1", actor: "admin" },
    { id: "email", day: "Oct 19, 2026", at: "2026-10-19T09:34:00-04:00", time: "09:34 AM", kind: "Conversations", icon: "comments", title: "Cloudflare invoice September", detail: "Arrived in AP Inbox", actor: "billing@cloudflare.com" },
  ],
  knowledge: [
    { id: "decision", day: "Oct 20, 2026", at: "2026-10-20T09:41:00-04:00", time: "09:41 AM", kind: "Decisions", icon: "help", title: "Review contract deviations", detail: `Asked on bill ${bill.number}`, actor: "Bill import", needsMe: true, tone: "warning" },
    { id: "updated", day: "Oct 18, 2026", at: "2026-10-18T18:47:00-04:00", time: "06:47 PM", kind: "Changes", icon: "pencil", title: "Updated", detail: "Rate clause", actor: "admin" },
    { id: "comment", day: "Oct 18, 2026", at: "2026-10-18T18:40:00-04:00", time: "06:40 PM", kind: "Comments", icon: "comments", title: "Added the October PO reference", actor: "admin" },
    { id: "created", day: "Oct 15, 2026", at: "2026-10-15T14:00:00-04:00", time: "02:00 PM", kind: "Changes", icon: "notes", title: "Created", detail: "From the supplier contracts template", actor: "admin" },
  ],
  nexus: [
    { id: "message", day: "Oct 20, 2026", at: "2026-10-20T08:35:00-04:00", time: "08:35 AM", kind: "Conversations", icon: "comments", title: "Sell on TikTok Shop Before Holiday Season", detail: "Received", actor: "Taylor from TikTok" },
    { id: "sync", day: "Oct 20, 2026", at: "2026-10-20T08:35:00-04:00", time: "08:35 AM", kind: "Sources", icon: "link", title: "Pulled from AP Inbox", detail: "Email", actor: "Mail sync" },
    { id: "earlier", day: "Oct 18, 2026", at: "2026-10-18T10:12:00-04:00", time: "10:12 AM", kind: "Conversations", icon: "comments", title: "Start Selling on TikTok Shop Today", detail: "Received", actor: "Taylor from TikTok" },
  ],
};
export const timelineKinds = ["Runs", "Decisions", "Changes", "Comments", "Tasks", "Files", "Pages", "Conversations", "Sources"];
export const needsAttention = (page: PageKind) => timelines[page].filter((entry) => entry.needsMe).length;
export const runSteps = [
  { title: "Started manually", detail: `admin · ${bill.file}`, time: "09:35 AM", state: "started" },
  { title: "File read", detail: "UBL 2.1 · Printed tax €0.00", time: "09:35 AM", state: "succeeded" },
  { title: "Company resolved", detail: bill.company, time: "09:36 AM", state: "succeeded", warning: `Printed buyer ${bill.printedBuyer} does not match ${bill.company}.` },
  { title: "Draft bill created", detail: `${bill.number} · Due ${bill.dueDate}`, time: "09:36 AM", state: "succeeded" },
  { title: "Check for duplicates", detail: "admin · Chose: Not a duplicate", time: "09:37 AM", state: "succeeded" },
  { title: "Supplier matched", detail: `${bill.supplier} · Correction applied`, time: "09:38 AM", state: "succeeded" },
  { title: "Agreement matched", detail: bill.agreement, time: "09:40 AM", state: "succeeded" },
  { title: "Review contract deviations", detail: "Hourly rate €100.00 · Agreement €90.00", time: "09:41 AM", state: "waiting" },
];
export const decision = {
  id: "target-contract-review", kind: "review", kind_label: "Review contract deviations",
  revision: 1, created_at: "2026-10-20T09:41:00-04:00", is_open: true, permissions: ["act"],
  context: { reason: "The consulting rate is €100.00 per hour; the agreement specifies €90.00.", facts: [], references: [] },
  verdict: null, verdict_values: null, verdict_label: "", answered_at: null,
  records: [], requester: { display_name: "Bill import" },
  assignees: [{ display_name: "admin" }, { display_name: "Walker Member" }], answered_by: null,
  proposal: { multiple: false, alternatives: [
    { key: "accept", label: "Accept the billed rate", outcome: "accepted", actions: {} },
    { key: "credit", label: "Request a credit note", outcome: "review", actions: {} },
  ] },
};

export type RelatedGroup = { id: string; label: string; verb?: string; rows: { title: string; detail: string; icon: string }[] };
export const related: Record<PageKind, RelatedGroup[]> = {
  bill: [
    { id: "files", label: "Files", verb: "Attach", rows: [{ title: bill.file, detail: "Source document · XML · 5.8 KB", icon: "file" }] },
    { id: "pages", label: "Pages", verb: "Bind", rows: [{ title: bill.agreement, detail: "Supplier contract", icon: "notes" }] },
    { id: "runs", label: "Runs", rows: [{ title: "Bill import", detail: "Waiting · 1 open decision", icon: "activity" }] },
    { id: "sessions", label: "Sessions", rows: [{ title: "Bill import assistant", detail: "Started by admin · Oct 20, 09:35 AM", icon: "agent" }] },
    { id: "conversations", label: "Conversations", rows: [{ title: `Re: Invoice ${bill.number}`, detail: "AP Inbox · 3 messages", icon: "comments" }] },
    { id: "sources", label: "Sources", verb: "Odoo", rows: [{ title: "Vendor bill · Odoo", detail: "us.apexive.com · Pulled Oct 17, 08:00 AM", icon: "link" }] },
    { id: "access", label: "Shared with", rows: access.map((person) => ({ title: person.name, detail: person.name === "admin" ? "Owner" : "Can read", icon: "user" })) },
  ],
  file: [{ id: "attached", label: "Attached to", rows: [{ title: `${bill.number} · ${bill.supplier}`, detail: "Vendor bill · Source document", icon: "file" }] }],
  knowledge: [
    { id: "backlinks", label: "Backlinks", rows: [{ title: "AP policy · Supplier contracts", detail: "Accounts Payable / Policies", icon: "notes" }, { title: "October close", detail: "Accounts Payable / Runbooks", icon: "notes" }] },
    { id: "bound", label: "Bound to", rows: [{ title: `PO/2026/00001 · ${bill.supplier}`, detail: "Contract · Purchase order", icon: "link" }, { title: bill.number, detail: "Contract · Vendor bill", icon: "link" }] },
  ],
  nexus: [
    { id: "sender", label: "Sender", rows: [{ title: "Taylor from TikTok", detail: "taylor@shop.tiktok.com", icon: "user" }] },
    { id: "conversations", label: "Conversations", rows: [{ title: "Sell on TikTok Shop Before Holiday Season", detail: "AP Inbox · Oct 20, 08:35 AM", icon: "comments" }, { title: "Start Selling on TikTok Shop Today", detail: "AP Inbox · Oct 18, 10:12 AM", icon: "comments" }] },
    { id: "sources", label: "Sources", rows: [{ title: "AP Inbox", detail: "Email · Taylor's messages", icon: "link" }] },
  ],
};
export const discussion = [
  { id: "walker", author: "Walker Member", initials: "WM", time: "05:02 PM", text: "Rate on line 1 is above the agreement; asked Cloudflare for a credit note before we post.", note: false },
  { id: "admin", author: "admin", initials: "AD", time: "05:20 PM", text: "Hold the contract step until they answer.", note: true },
];
export const tasks = {
  bill: [
    { title: "Verify the printed bank account", detail: "Due Oct 21 · Walker Member" },
    { title: "Chase the credit note", detail: "Due Oct 23 · admin" },
  ],
  knowledge: [{ title: "Review the October rate clause", detail: "Due Oct 21 · admin" }],
};

export const senders = [
  { name: "Taylor from TikTok", address: "taylor@shop.tiktok.com", count: 38 },
  { name: "quarantine@localhost", address: "Spam Quarantine Notification", count: 360 },
  { name: "Jay", address: "Python / Django Engineer", count: 1 },
  { name: "Andrew Tsoy", address: "tsoy4ku@gmail.com", count: 1 },
  { name: "Mukaram Khan", address: "Application for Flutter Developer", count: 1 },
  { name: "Fiona Grant", address: "fiona.grantt@gmail.com", count: 1 },
  { name: "Timofey", address: "Senior Python / Django engineer", count: 1 },
  { name: "Mohamed Salem", address: "Flutter & React / Next.js developer", count: 1 },
  { name: "Mia Brooks", address: "Explore New SEO Placement Opportunities", count: 1 },
  { name: "Plaid", address: "billing@plaid.com", count: 30 },
];
export const lensOptions = [
  { value: "Senders", label: "Senders · 1,462" },
  { value: "Recipients", label: "Recipients · 184" },
  { value: "Circles", label: "Circles · 6" },
];
export const lensRows = {
  Senders: senders,
  Recipients: [{ name: "AP Inbox", address: "Apexive Solutions LLC", count: 4010 }],
  Circles: [
    { name: "Suppliers", address: "Supplier correspondence", count: 12 },
    { name: "Customers", address: "Customer correspondence", count: 4 },
    { name: "AP team", address: "Internal conversations", count: 4 },
    { name: "Recruitment", address: "Applications", count: 4 },
    { name: "Work", address: "Work conversations", count: 4 },
    { name: "Everyone", address: "All contacts", count: 1462 },
  ],
};
export type InboxLens = keyof typeof lensRows;
export const navigatorCounts = { bills: 19, senders: 1462, unread: 38, messages: 4010 };
export const conversations = [
  { id: "holiday", day: "Oct 20, 2026", time: "08:35 AM", title: "Sell on TikTok Shop Before Holiday Season", snippet: "Get your shop ready for the holiday season. Here are the next steps to start selling.", count: 1 },
  { id: "start", day: "Oct 18, 2026", time: "10:12 AM", title: "Start Selling on TikTok Shop Today", snippet: "Your next customers are discovering products on TikTok. Set up your seller account.", count: 1 },
  { id: "welcome", day: "Oct 16, 2026", time: "09:20 AM", title: "Welcome to TikTok Shop", snippet: "A guide to preparing your first product listing and completing your shop profile.", count: 2 },
];
export const message = {
  subject: conversations[0]!.title,
  from: "Taylor from TikTok",
  email: "taylor@shop.tiktok.com",
  to: "AP Inbox · Apexive Solutions LLC",
  body: "Hi Apexive team,\n\nThe holiday season is coming. Get your shop ready so customers can discover your products on TikTok.\n\nComplete your seller profile, add your first products, and review your shipping settings. Your seller dashboard has the checklist to help you get started.\n\nIf you have questions about setting up your shop, reply to this email and our team will help.\n\nTaylor\nTikTok Shop",
};
export const recordHeaders = {
  bill: { eyebrow: `Vendor bill · ${bill.company}`, title: `${bill.number} · ${bill.supplier}`, facts: `${bill.amount} · Due ${bill.dueDate} · ${bill.payment}` },
  file: { eyebrow: "File · AP drive / 2026 / October", title: bill.file, facts: `XML · ${bill.size} · Uploaded Oct 19, 2026 by admin` },
  knowledge: { eyebrow: "Knowledge page · Accounts Payable / Supplier contracts", title: bill.agreement, facts: "Updated Oct 18, 2026, 06:47 PM · admin · Published" },
  nexus: { eyebrow: "Inbox · Nexus / Senders / Taylor from TikTok", title: message.from, facts: `${message.email} · 38 messages · All accounts` },
  nexusMessage: { eyebrow: "Message · Nexus / AP Inbox / Taylor from TikTok", title: message.subject, facts: "Email · Received Oct 20, 2026, 08:35 AM · AP Inbox" },
};
export const fileDetails = {
  type: "application/xml", uploaded: "Oct 19, 2026 · admin", drive: "AP drive", folder: "2026 / October",
};
export const agreementMarkdown = `## Cloudflare, Inc. — remote consulting agreement

Scope: remote consulting billed per hour at **EUR 90.00**, printed manuals at list price. Purchase order **PO/2026/00001** implements this agreement for October 2026.

Deviations from the agreed hourly rate are reviewed before posting.`;

export const lineMetadata = {
  field: "lines", modelLabel: "target.InvoiceLine", positionField: "position",
  fields: [
    { name: "label", label: "Label", scalar: "String" },
    { name: "quantity", label: "Qty", scalar: "Decimal" },
    { name: "unit", label: "Unit price", scalar: "Decimal" },
  ].map((field) => ({
    ...field, kind: "scalar", readable: true, filterable: false, sortable: false,
    aggregatable: false, groupable: false, creatable: true, updatable: true, requiredOnCreate: false,
  })),
} satisfies DataResourceLinesMetadata;
export const invoiceXml = `<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2">
  <ID>SYN-UBL-9311</ID>
  <IssueDate>2026-09-26</IssueDate>
  <DueDate>2026-10-15</DueDate>
  <Supplier>Cloudflare, Inc.</Supplier>
  <Buyer>Synthetic Buyer BV</Buyer>
  <TaxAmount currencyID="EUR">0.00</TaxAmount>
  <PayableAmount currencyID="EUR">307.14</PayableAmount>
</Invoice>`;
