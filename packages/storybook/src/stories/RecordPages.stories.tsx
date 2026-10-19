import type { Meta, StoryObj } from "@storybook/react-vite";
import { Badge } from "@angee/ui";
import { RecordPage, type RecordPageProps } from "./record-page/RecordPage";
import { BodyTabs, Field, FieldGrid, LinesTable, Placeholder } from "./record-page/Subject";

/**
 * Four record pages from one composition: invoice, file, knowledge page, person. The components
 * under `./record-page` are the reusable parts (explorer pane, command band, subject, document
 * pane, context aside with its four panes, pane toolbar); each page only supplies data.
 */

const t = (daysAgo: number, hh: number, mm: number) => new Date(2026, 9, 19 - daysAgo, hh, mm).toISOString();

/* Invoice ------------------------------------------------------------------------------------ */

const INVOICE: RecordPageProps = {
  explorer: {
    root: "Vendor bills", view: "To review", create: "New bill",
    rows: [
      { label: "SYN-UBL-9506 · Cloudflare, Inc.", meta: "€307.14", active: true, tone: "warning" },
      { label: "SYN-UBL-9507 · Cloudflare, Inc.", meta: "€307.14", tone: "warning" },
      { label: "INV-2211 · Hetzner Online", meta: "€48.00", tone: "success" },
      { label: "INV-0931 · Notion Labs", meta: "$96.00", tone: "success" },
      { label: "M/5/5/R1 · Piloto 151", meta: "$75.00", tone: "success" },
    ],
  },
  subject: {
    eyebrow: "Vendor bill · Apexive Solutions LLC", title: "SYN-UBL-9506 · Cloudflare, Inc.", subtitle: "€307.14 · due Oct 28, 2026 · not paid",
    steps: [{ value: "draft", label: "Draft" }, { value: "posted", label: "Posted" }, { value: "cancelled", label: "Cancelled" }], step: "draft",
    waits: "Posting waits for 1 open decision",
    lifecycle: [{ label: "Post" }, { label: "Cancel", tone: "danger" }],
    menu: ["Pay…", "Reverse", "Send by email", "Download PDF", "Assign accounting", "Run workflow…", "Rate…"],
    controls: { access: ["AD", "WM", "AP"], following: true, needsMe: 1, pager: "1 / 19" },
    body: <>
      <FieldGrid>
        <Field label="Supplier" value="Cloudflare, Inc." mark="Correction" /><Field label="Invoice date" value="Oct 14, 2026" /><Field label="Accounting date" value="Oct 14, 2026" />
        <Field label="Bill number" value="SYN-UBL-9506" /><Field label="Journal" value="Vendor Bills" /><Field label="Payment term" value="—" />
        <Field label="Currency" value="EUR — Euro" /><Field label="Payment status" value={<Badge tone="warning" variant="soft">Not paid</Badge>} /><Field label="Bank account" value="—" />
      </FieldGrid>
      <BodyTabs tabs={["Invoice lines", "Journal items"]} active="Invoice lines" />
      <LinesTable rows={[["Remote consulting, October", "2", "100.00", "€200.00"], ["Printed manual", "3", "35.71", "€107.14"]]} />
      <div className="mt-3 flex justify-end gap-6 text-13"><span className="text-fg-muted">Untaxed €307.14</span><span className="text-fg-muted">Tax €0.00</span><span className="font-semibold">Total €307.14</span></div>
    </>,
    document: { title: "v8-cloudflare-9506-po-deviation.xml", meta: "UBL · 6 KB", preview: <Placeholder label="rendered e-invoice" h="100%" /> },
  },
  aside: {
    kinds: ["run", "decision", "change", "comment", "conversation", "task", "file", "source", "access"],
    lines: [
      { id: "l1", at: t(0, 9, 41), kind: "run", title: "Bill import", actor: "Bill import", detail: "waiting · Match the supplier contract", needsMe: true, folded: "3 changes · 5 decisions" },
      { id: "l2", at: t(0, 9, 12), kind: "access", title: "Shared with Walker Member", actor: "admin", detail: "read" },
      { id: "l3", at: t(1, 17, 2), kind: "comment", title: "Rate on line 1 is above the agreement; asked Cloudflare…", actor: "Walker Member" },
      { id: "l4", at: t(1, 16, 40), kind: "conversation", title: "Re: Invoice SYN-UBL-9506", actor: "AP Inbox", detail: "3 messages" },
      { id: "l5", at: t(1, 16, 30), kind: "task", title: "Verify the printed bank account", actor: "Walker Member", detail: "scheduled for Oct 21" },
      { id: "l6", at: t(1, 9, 35), kind: "file", title: "v8-cloudflare-9506-po-deviation.xml", actor: "admin", detail: "attached" },
      { id: "l7", at: t(2, 8, 0), kind: "source", title: "Synced from Odoo", actor: "us.apexive.com", detail: "vendor bill 1402" },
    ],
    discussion: { messages: [
      { who: "Walker Member", at: t(1, 17, 2), text: "Rate on line 1 is above the agreement; asked Cloudflare for a credit note before we post." },
      { who: "admin", at: t(1, 17, 20), text: "Hold the contract step until they answer.", kind: "note" },
    ] },
    tasks: [{ title: "Verify the printed bank account", due: "Oct 21", who: "Walker Member" }, { title: "Chase the credit note", due: "Oct 23", who: "admin" }, { title: "Resolve the company", due: "Oct 18", who: "Bill import", done: true }],
    related: [
      { id: "files", label: "Files", count: 1, verb: "Upload", rows: [{ label: "v8-cloudflare-9506-po-deviation.xml", meta: "UBL · 6 KB" }] },
      { id: "sources", label: "Sources", count: 1, rows: [{ label: "Odoo · us.apexive.com · vendor bill 1402", meta: "synced Oct 17" }] },
      { id: "conversations", label: "Conversations", count: 1, rows: [{ label: "Re: Invoice SYN-UBL-9506", meta: "AP Inbox · 3" }] },
    ],
  },
};

/* File --------------------------------------------------------------------------------------- */

const FILE: RecordPageProps = {
  explorer: {
    root: "AP drive", view: "All files", create: "New folder",
    rows: [{ label: "Inbox", icon: "folder" }, { label: "2026", icon: "folder" }, { label: "October", icon: "folder", depth: 1 }, { label: "v8-cloudflare-9506-po-deviation.xml", icon: "file", depth: 2, active: true }, { label: "hetzner-2211.pdf", icon: "file", depth: 2 }, { label: "Contracts", icon: "folder" }],
  },
  subject: {
    eyebrow: "File · AP drive / 2026 / October", title: "v8-cloudflare-9506-po-deviation.xml", subtitle: "UBL · 6 KB · uploaded Oct 18, 2026 by admin",
    lifecycle: [], menu: ["Download", "Read again (import bill)", "Move…", "Rename", "Trash"],
    controls: { access: ["AD", "WM"], following: false, needsMe: 0, pager: "4 / 12" },
    body: <>
      <Placeholder label="rendered e-invoice · the body is the file" h="24rem" />
      <BodyTabs tabs={["Details", "Versions"]} active="Details" />
      <div className="mt-4"><FieldGrid><Field label="Type" value="application/xml" /><Field label="Size" value="6 KB" /><Field label="Checksum" value="sha256 · 9f3a…" /><Field label="Uploaded" value="Oct 18, 2026 · admin" /><Field label="Drive" value="AP drive" /><Field label="Folder" value="2026 / October" /></FieldGrid></div>
    </>,
  },
  aside: {
    kinds: ["run", "change", "conversation", "source", "access"],
    lines: [
      { id: "f1", at: t(1, 20, 15), kind: "run", title: "Bill import", actor: "admin", detail: "waiting · Check for duplicates", needsMe: true, folded: "1 change · 1 decision" },
      { id: "f2", at: t(1, 20, 14), kind: "change", title: "Created", actor: "admin", detail: "upload" },
      { id: "f3", at: t(1, 20, 13), kind: "conversation", title: "Fwd: Cloudflare invoice October", actor: "billing@cloudflare.com", detail: "arrived in AP Inbox" },
    ],
    related: [
      { id: "attached-to", label: "Attached to", count: 1, rows: [{ label: "Vendor bill SYN-UBL-9507 · Cloudflare, Inc.", meta: "source document" }] },
      { id: "conversations", label: "Conversations", count: 1, rows: [{ label: "Fwd: Cloudflare invoice October", meta: "AP Inbox" }] },
    ],
  },
};

/* Knowledge page ----------------------------------------------------------------------------- */

const PAGE: RecordPageProps = {
  explorer: {
    root: "Accounts Payable", view: "Pages", create: "New page",
    rows: [{ label: "Policies", icon: "folder" }, { label: "Supplier contracts", icon: "folder" }, { label: "Cloudflare, Inc. agreement", icon: "notes", depth: 1, active: true }, { label: "Hetzner agreement", icon: "notes", depth: 1 }, { label: "Runbooks", icon: "folder" }, { label: "October close", icon: "notes", depth: 1 }],
  },
  subject: {
    eyebrow: "Knowledge page · Accounts Payable / Supplier contracts", title: "Cloudflare, Inc. agreement", subtitle: "edited Oct 18, 2026 by admin · published",
    lifecycle: [{ label: "Publish" }], menu: ["Move…", "Duplicate", "Export", "Trash"],
    controls: { access: ["AD", "WM", "AP"], following: true, needsMe: 0, pager: "2 / 6" },
    body: <>
      <p className="text-13 leading-6">Scope: remote consulting billed per hour at <b>EUR 90.00</b>, printed manuals at list price. Purchase order PO/2026/00001 implements this agreement for October 2026. Deviations from the agreed hourly rate are reviewed before posting.</p>
      <div className="mt-4"><Placeholder label="editor · the body is the page" h="18rem" /></div>
    </>,
  },
  aside: {
    kinds: ["change", "comment", "decision", "task", "run", "access"],
    lines: [
      { id: "p1", at: t(0, 9, 41), kind: "decision", title: "Review contract deviations", actor: "Bill import", detail: "asked on bill SYN-UBL-9506", needsMe: true },
      { id: "p2", at: t(1, 11, 5), kind: "change", title: "Updated", actor: "admin", detail: "rate clause" },
      { id: "p3", at: t(1, 10, 50), kind: "comment", title: "Added the October PO reference.", actor: "admin" },
      { id: "p4", at: t(4, 14, 0), kind: "change", title: "Created", actor: "admin", detail: "from the contracts template" },
    ],
    discussion: { messages: [{ who: "admin", at: t(1, 10, 50), text: "Added the October PO reference." }] },
    tasks: [{ title: "Renew before Dec 31", due: "Dec 15", who: "admin" }],
    related: [
      { id: "bound-to", label: "Bound to", count: 1, rows: [{ label: "PO/2026/00001 · Cloudflare, Inc.", meta: "contract" }] },
      { id: "backlinks", label: "Backlinks", count: 2, rows: [{ label: "AP policy · supplier contracts", meta: "Policies" }, { label: "October close", meta: "Runbooks" }] },
      { id: "files", label: "Files", count: 0, verb: "Upload", rows: [] },
    ],
  },
};

/* Person (nexus) ----------------------------------------------------------------------------- */

const PERSON: RecordPageProps = {
  explorer: {
    root: "Parties", view: "Suppliers", lenses: [{ label: "Ego", active: true }, { label: "Circle" }, { label: "Identity" }],
    rows: [{ label: "Cloudflare, Inc.", icon: "users" }, { label: "Jane Doe · billing", depth: 1, active: true }, { label: "Tom Lin · account manager", depth: 1 }, { label: "Hetzner Online", icon: "users" }, { label: "Notion Labs", icon: "users" }],
  },
  subject: {
    eyebrow: "Person · Cloudflare, Inc.", title: "Jane Doe", subtitle: "billing contact · jane@cloudflare.com · +1 650 …",
    lifecycle: [], menu: ["Merge…", "Add email", "Add phone", "Run workflow…"],
    controls: { access: ["AD", "WM"], following: false, needsMe: 0, pager: "2 / 2" },
    body: <>
      <FieldGrid><Field label="Organization" value="Cloudflare, Inc." mark="Source" /><Field label="Role" value="Billing" /><Field label="Circles" value="Suppliers" /><Field label="Address" value="101 Townsend St, San Francisco" /><Field label="Identity" value="verified · Odoo partner 2081" /><Field label="Handles" value="2 (email, phone)" /></FieldGrid>
      <BodyTabs tabs={["Handles", "Identity", "Relationships", "Addresses", "Network", "Accounting"]} active="Network" />
      <div className="mt-4"><Placeholder label="network · the party's ties (the graph page has the lenses)" h="14rem" /></div>
    </>,
  },
  aside: {
    kinds: ["correspondence", "change", "run", "decision", "task", "file", "source", "access"],
    lines: [
      { id: "n1", at: t(0, 8, 30), kind: "correspondence", title: "Re: Invoice SYN-UBL-9506 · credit note?", actor: "Jane Doe", detail: "email · AP Inbox" },
      { id: "n2", at: t(1, 16, 40), kind: "correspondence", title: "Invoice SYN-UBL-9506", actor: "Walker Member", detail: "email sent" },
      { id: "n3", at: t(2, 10, 0), kind: "run", title: "Supplier onboarding", actor: "admin", detail: "done · verified", folded: "2 changes · 1 decision" },
      { id: "n4", at: t(2, 9, 58), kind: "change", title: "Updated", actor: "Odoo · us.apexive.com", detail: "handles, address" },
      { id: "n5", at: t(9, 12, 0), kind: "access", title: "Shared with AP seats", actor: "admin", detail: "read private" },
    ],
    tasks: [{ title: "Confirm the bank account by phone", due: "Oct 21", who: "Walker Member" }],
    related: [
      { id: "files", label: "Files", count: 1, verb: "Upload", rows: [{ label: "W-9 Cloudflare.pdf", meta: "PDF · 210 KB" }] },
      { id: "sources", label: "Sources", count: 1, rows: [{ label: "Odoo · us.apexive.com · partner 2081", meta: "synced Oct 17" }] },
    ],
  },
};

/* ------------------------------------------------------------------------------------------- */

const meta = {
  title: "Shell/Record pages",
  component: RecordPage,
  excludeStories: [],
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof RecordPage>;
export default meta;
type Story = StoryObj<typeof meta>;

/** Invoice: lifecycle verbs on the band, capability verbs in the menu, the source document beside the form, the import run folded on the Timeline. */
export const Invoice: Story = { args: INVOICE };
/** The invoice's Actions menu: pay, reverse, send, PDF, run workflow and rate live here, never on the band. */
export const InvoiceActions: Story = { args: { ...INVOICE, menuOpen: true } };
/** "Open run" from the Timeline: the drill-in replaces the rail; back and title lead its toolbar; Stop is this run's. */
export const InvoiceRunOpen: Story = { args: { ...INVOICE, drillIn: "run" } };
/** Discussion: comments, an internal note, a composer that says who will see it. */
export const InvoiceDiscussion: Story = { args: { ...INVOICE, tab: "discussion" } };
/** Related: the owner's verb per group, one row grammar for every linked thing. */
export const InvoiceRelated: Story = { args: { ...INVOICE, tab: "related" } };
/** File: the drive tree explores, the preview is the body, Details a body tab; no Discussion, no Tasks; Related shows what it is attached to. */
export const File: Story = { args: FILE };
/** Knowledge page: the vault tree explores, the editor is the body; Related shows what the page is bound to and its backlinks. */
export const KnowledgePage: Story = { args: PAGE };
/** Person: the parties navigator with its lenses explores, Network is a body tab, correspondence is a Timeline kind; parties are not discussable. */
export const Person: Story = { args: PERSON };
