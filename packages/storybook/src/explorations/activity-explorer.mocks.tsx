import * as React from "react";
import { Avatar, Badge, Button, Chip, StatusIcon, StepList, type StepListItem } from "@angee/ui";

/** Shared whiteboard mocks for the Activity explorer and the timeline + drill-in stories. Not wired to sources. */

export type Kind = "comment" | "change" | "run" | "decision" | "conversation" | "source" | "task" | "file";

export interface Actor { initials: string; name: string; kind?: "person" | "system" | "agent" }

export interface RunStep { id: string; title: string; state: StepListItem["state"]; note?: string; tone?: StepListItem["tone"]; decision?: DecisionCardData; at?: string }

export interface DecisionCardData {
  title: string;
  reason: string;
  alternatives: readonly string[];
  mayAnswer: string;
  facts?: readonly [string, string][];
}

export interface Entry {
  id: string;
  kind: Kind;
  at: string;
  actor: Actor;
  needsMe?: boolean;
  /** run */
  run?: { name: string; status: "Waiting" | "Running" | "Done" | "Failed" | "Stopped"; steps: readonly RunStep[]; doneSummary?: string };
  /** change */
  change?: { fields: readonly { field: string; from: string; to: string; source?: string }[]; reason?: string; revertable?: boolean };
  /** decision answered */
  verdict?: { kind: string; chosen: string; byRole: string };
  /** comment / conversation / source / task / file */
  text?: string;
  reference?: { label: string; meta?: string };
  attachments?: readonly string[];
}

export const KIND_META: Record<Kind, { label: string; tone: "info" | "success" | "warning" | "danger" | "neutral" | "brand"; icon: string }> = {
  comment: { label: "Comments", tone: "brand", icon: "comments" },
  change: { label: "Changes", tone: "neutral", icon: "pencil" },
  run: { label: "Runs", tone: "info", icon: "activity" },
  decision: { label: "Decisions", tone: "warning", icon: "help" },
  conversation: { label: "Conversations", tone: "brand", icon: "users" },
  source: { label: "Sources", tone: "neutral", icon: "link" },
  task: { label: "Tasks", tone: "success", icon: "check" },
  file: { label: "Files", tone: "neutral", icon: "file" },
};

export const admin: Actor = { initials: "AD", name: "admin" };
export const walker: Actor = { initials: "WM", name: "Walker Member" };
export const importer: Actor = { initials: "BI", name: "Bill import", kind: "system" };
export const odoo: Actor = { initials: "OD", name: "Odoo · us.apexive.com", kind: "system" };
export const reader: Actor = { initials: "Q", name: "qwen3.6:35b", kind: "agent" };

export const t = (daysAgo: number, hh: number, mm: number): string => {
  const d = new Date(2026, 9, 19 - daysAgo, hh, mm, 0);
  return d.toISOString();
};

export const contractDecision: DecisionCardData = {
  title: "Review contract deviations",
  reason: "The supplier bill differs from its referenced purchase order.",
  alternatives: ["Accept the deviation and retain a note", "Tag as a contract discrepancy", "Hold for the purchase-order buyer"],
  mayAnswer: "admin, Walker Member",
  facts: [["Rate · line 1", "actual 100.00 · expected 90.00"], ["Agreement", "Cloudflare, Inc. agreement"]],
};

export const billEntries: readonly Entry[] = [
  {
    id: "run-import", kind: "run", at: t(0, 9, 41), actor: importer, needsMe: true,
    run: {
      name: "Bill import", status: "Waiting", doneSummary: "Read the file, Resolve the company, Create the draft bill, Duplicate check, Choose the supplier, Review payment risk, Activate supplier, Confirm fields, Taxes",
      steps: [
        { id: "s-done", title: "9 steps done", state: "done", at: t(0, 9, 36), note: "Structured e-invoice found · Printed tax 0.00 EUR · Printed buyer does not match Apexive Solutions LLC" },
        { id: "s-contract", title: "Match the supplier contract", state: "current", at: t(0, 9, 41), decision: contractDecision },
        { id: "s-integrity", title: "Review bill integrity", state: "planned" },
        { id: "s-threshold", title: "Approve bill threshold", state: "planned" },
        { id: "s-ready", title: "Ready to post", state: "planned" },
      ],
    },
  },
  { id: "dec-risk", kind: "decision", at: t(0, 9, 38), actor: admin, verdict: { kind: "Review payment risk", chosen: "Continue with human payment controls", byRole: "ap_reviewer" } },
  {
    id: "chg-supplier", kind: "change", at: t(0, 9, 37), actor: importer,
    change: { reason: "Choose the supplier · Keep the stored tax ID", fields: [{ field: "Supplier", from: "—", to: "Cloudflare, Inc.", source: "statement · supplier_choice" }], revertable: true },
  },
  { id: "dec-supplier", kind: "decision", at: t(0, 9, 37), actor: admin, verdict: { kind: "Choose the supplier", chosen: "Use Cloudflare, Inc. · keep the stored tax ID", byRole: "ap_reviewer" } },
  {
    id: "chg-create", kind: "change", at: t(0, 9, 36), actor: reader,
    change: { reason: "Create the draft bill", fields: [{ field: "Bill number", from: "—", to: "SYN-UBL-9506", source: "v8-cloudflare-9506.xml" }, { field: "Due date", from: "—", to: "Oct 28, 2026", source: "printed due date" }, { field: "Total", from: "—", to: "€307.14", source: "printed totals" }] },
  },
  { id: "src-file", kind: "source", at: t(0, 9, 35), actor: admin, text: "Uploaded v8-cloudflare-9506-po-deviation.xml", reference: { label: "v8-cloudflare-9506-po-deviation.xml", meta: "UBL · 6 KB" } },
  { id: "cmt-1", kind: "comment", at: t(1, 17, 2), actor: walker, text: "Rate on line 1 is above the agreement; asked Cloudflare for a credit note before we post.", attachments: ["cloudflare-thread.eml"] },
  { id: "conv-1", kind: "conversation", at: t(1, 16, 40), actor: walker, text: "Re: Invoice SYN-UBL-9506 · 3 messages", reference: { label: "Open conversation", meta: "AP Inbox" } },
  { id: "task-1", kind: "task", at: t(1, 16, 30), actor: walker, text: "Verify the printed bank account with the supplier", reference: { label: "Open task", meta: "due Oct 21" } },
];

export const fileEntries: readonly Entry[] = [
  { id: "f-run", kind: "run", at: t(0, 9, 41), actor: importer, run: { name: "Bill import", status: "Done", doneSummary: "Read the file, Resolve the company, Create the draft bill", steps: [
    { id: "f-s1", title: "3 steps done", state: "done", at: t(0, 9, 36) },
    { id: "f-s2", title: "Opened bill SYN-UBL-9506", state: "done", at: t(0, 9, 41), tone: "success", note: "The bill carries the rest of this file's story." },
  ] } },
  { id: "f-src", kind: "source", at: t(0, 9, 35), actor: admin, text: "Uploaded to Admin's drive", reference: { label: "Admin's drive", meta: "6 KB · application/xml" } },
  { id: "f-chg", kind: "change", at: t(0, 9, 35), actor: admin, change: { reason: "Upload", fields: [{ field: "Filename", from: "—", to: "v8-cloudflare-9506-po-deviation.xml" }] } },
];

export const partyEntries: readonly Entry[] = [
  { id: "p-dec", kind: "decision", at: t(0, 9, 38), actor: admin, verdict: { kind: "Activate supplier", chosen: "Keep supplier pending", byRole: "ap_reviewer" }, needsMe: false },
  { id: "p-chg", kind: "change", at: t(0, 9, 37), actor: importer, change: { reason: "Supplier onboarded", fields: [{ field: "Tax ID", from: "—", to: "DE900009404", source: "printed · kept for review" }, { field: "Address", from: "—", to: "Musterweg 12, Berlin", source: "printed" }] } },
  { id: "p-src", kind: "source", at: t(3, 21, 47), actor: odoo, text: "Partner synced from Odoo (res.partner 1)", reference: { label: "Open in Odoo", meta: "us.apexive.com" } },
  { id: "p-conv", kind: "conversation", at: t(1, 16, 40), actor: walker, text: "Re: Invoice SYN-UBL-9506 · 3 messages", reference: { label: "Open conversation", meta: "AP Inbox" } },
  { id: "p-file", kind: "file", at: t(0, 9, 35), actor: admin, text: "v8-cloudflare-9506-po-deviation.xml attached through bill SYN-UBL-9506", reference: { label: "Open file" } },
  { id: "p-task", kind: "task", at: t(1, 16, 30), actor: walker, text: "Verify the printed bank account with the supplier", reference: { label: "Open task", meta: "due Oct 21" } },
];

/* ------------------------------------------------------------------------------------------ */

export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date(2026, 9, 19);
  const diff = Math.round((today.getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86_400_000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function ActorAvatar({ actor }: { actor: Actor }): React.ReactElement {
  return <Avatar size="sm" initials={actor.initials} />;
}

export function KindTag({ kind }: { kind: Kind }): React.ReactElement {
  const meta = KIND_META[kind];
  return <Chip tone={meta.tone} variant="soft" className="text-11 uppercase tracking-wide">{meta.label.replace(/s$/, "")}</Chip>;
}

export function DecisionCardMock({ card, answered }: { card: DecisionCardData; answered?: string }): React.ReactElement {
  return <section className="mt-2 rounded-8 border border-border-strong bg-sheet p-3 shadow-xs" aria-label={card.title}>
    <div className="flex items-start gap-2">
      <StatusIcon tone="warning" icon="help" size="sm" />
      <div className="min-w-0 flex-1">
        <p className="text-13 font-semibold">{card.title}</p>
        <p className="text-12 text-fg-muted">{card.reason}</p>
      </div>
    </div>
    {card.facts ? <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-12">
      {card.facts.map(([k, v]) => <React.Fragment key={k}><dt className="text-fg-muted">{k}</dt><dd className="font-medium">{v}</dd></React.Fragment>)}
    </dl> : null}
    <ul className="mt-2 space-y-1">
      {card.alternatives.map((alt) => <li key={alt} className={answered === alt ? "rounded-6 border border-border-strong bg-canvas px-2 py-1.5 text-13 font-medium" : "rounded-6 border border-border-subtle px-2 py-1.5 text-13"}>
        <span className="mr-2 inline-block size-3 rounded-full border border-border-strong align-middle" aria-hidden />{alt}
      </li>)}
    </ul>
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <Button size="sm">Confirm</Button>
      <Button size="sm" variant="ghost">Edit on form</Button>
      <span className="ml-auto text-11 text-fg-muted">May answer: {card.mayAnswer}</span>
    </div>
  </section>;
}

export function RunEntry({ entry, dense }: { entry: Entry; dense: boolean }): React.ReactElement {
  const run = entry.run!;
  const [showDone, setShowDone] = React.useState(false);
  const statusTone = run.status === "Waiting" ? "warning" : run.status === "Failed" ? "danger" : run.status === "Done" ? "success" : "info";
  const items: StepListItem[] = run.steps.map((step) => ({
    id: step.id,
    state: step.state,
    title: step.state === "done" && step.id.endsWith("done") && run.doneSummary
      ? <button type="button" className="text-link hover:underline" onClick={() => setShowDone((v) => !v)}>{showDone ? "Hide" : "Show"} {step.title}</button>
      : step.title,
    tone: step.tone,
    timestamp: step.at ?? null,
    detail: step.state === "done" && step.id.endsWith("done") && showDone ? run.doneSummary : step.note,
    children: step.decision ? <DecisionCardMock card={step.decision} /> : undefined,
  }));
  return <div className={dense ? "" : "rounded-8 border border-border-subtle bg-canvas/40 p-3"}>
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <span className="text-13 font-semibold">{run.name}</span>
      <Badge tone={statusTone} variant="soft">{run.status}</Badge>
      <span className="text-11 text-fg-muted">started {time(entry.at)}</span>
      <Button size="sm" variant="ghost" className="ml-auto">Open run</Button>
      {run.status === "Waiting" || run.status === "Running" ? <Button size="sm" variant="danger">Stop</Button> : null}
    </div>
    <StepList items={items} />
  </div>;
}

export function ChangeEntry({ entry }: { entry: Entry }): React.ReactElement {
  const change = entry.change!;
  return <div>
    {change.reason ? <p className="text-13 font-medium">{change.reason}</p> : null}
    <table className="mt-1 w-full text-12">
      <tbody>
        {change.fields.map((f) => <tr key={f.field} className="align-top">
          <td className="w-28 py-0.5 pr-2 text-fg-muted">{f.field}</td>
          <td className="py-0.5 pr-2"><span className="text-fg-muted line-through">{f.from}</span> <span aria-hidden>→</span> <span className="font-medium">{f.to}</span></td>
          <td className="py-0.5 text-right">{f.source ? <Chip tone="muted" variant="soft">{f.source}</Chip> : null}</td>
        </tr>)}
      </tbody>
    </table>
    {change.revertable ? <div className="mt-1"><Button size="sm" variant="ghost">Revert</Button><Button size="sm" variant="ghost">Open history</Button></div> : null}
  </div>;
}

export function VerdictEntry({ entry }: { entry: Entry }): React.ReactElement {
  const v = entry.verdict!;
  return <div className="flex flex-wrap items-center gap-2 text-13">
    <StatusIcon tone="success" icon="check" size="sm" />
    <span className="font-medium">{v.kind}</span>
    <span className="text-fg-muted">chose</span>
    <span className="font-medium">{v.chosen}</span>
    <Chip tone="muted" variant="soft">{v.byRole}</Chip>
  </div>;
}

export function ReferenceEntry({ entry }: { entry: Entry }): React.ReactElement {
  return <div className="text-13">
    <p>{entry.text}</p>
    {entry.reference ? <p className="mt-1 text-12"><a className="text-link hover:underline" href="#">{entry.reference.label}</a>{entry.reference.meta ? <span className="text-fg-muted"> · {entry.reference.meta}</span> : null}</p> : null}
    {entry.attachments?.length ? <div className="mt-1 flex flex-wrap gap-1">{entry.attachments.map((a) => <Chip key={a} tone="muted" variant="soft">{a}</Chip>)}</div> : null}
  </div>;
}

export function EntryBody({ entry, dense }: { entry: Entry; dense: boolean }): React.ReactElement {
  switch (entry.kind) {
    case "run": return <RunEntry entry={entry} dense={dense} />;
    case "change": return <ChangeEntry entry={entry} />;
    case "decision": return <VerdictEntry entry={entry} />;
    default: return <ReferenceEntry entry={entry} />;
  }
}

export function titleOf(entry: Entry): string {
  switch (entry.kind) {
    case "run": return `${entry.run!.name} · ${entry.run!.status.toLowerCase()}`;
    case "change": return `${entry.change!.fields.length === 1 ? "Changed" : `Changed ${entry.change!.fields.length} fields`}`;
    case "decision": return "Decided";
    case "comment": return "Commented";
    case "conversation": return "Conversation";
    case "source": return "Source";
    case "task": return "Task";
    case "file": return "File";
  }
}
