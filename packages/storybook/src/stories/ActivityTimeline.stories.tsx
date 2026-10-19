import type { Meta, StoryObj } from "@storybook/react-vite";
import * as React from "react";
import { Badge, Button, Chip, CountBadge, MessageFeed, MessageRow, StatusIcon, StepList, type StepListItem } from "@angee/ui";
import { ActorAvatar, DecisionCardMock, KIND_META, RunEntry, billEntries, dayLabel, time, type Entry, type Kind } from "./activity-explorer.mocks";

/**
 * Whiteboard prototype (2026-10-19, second direction): the record's aside is a compact
 * TIMELINE, not a chat. Every entry is one line on a vertical line: a glyph for its kind,
 * what happened, who, when. The entry's verb opens a SECOND aside beside it with the thing
 * itself: the workflow run (stepper, decision inline, the run's own chatter), the change
 * history (changesets, field diffs, Revert), the conversation (its messages), or the
 * record's discussion (comments + composer). The timeline stays visible; the drill-in
 * closes back to it. On a narrow viewport the drill-in replaces the rail with a back verb.
 */

type Panel =
  | { kind: "run"; id: string }
  | { kind: "history"; id: string; field?: string }
  | { kind: "conversation"; id: string }
  | { kind: "discussion" }
  | { kind: "decision"; id: string };

/* ---------------------------------------- the rail ---------------------------------------- */

function glyph(entry: Entry): React.ReactElement {
  const meta = KIND_META[entry.kind];
  const tone = entry.kind === "run" ? (entry.run!.status === "Waiting" ? "warning" : entry.run!.status === "Failed" ? "danger" : "info")
    : entry.kind === "decision" ? "success" : meta.tone === "brand" || meta.tone === "neutral" ? "info" : meta.tone;
  return <StatusIcon tone={tone} icon={meta.icon} size="sm" />;
}

function line(entry: Entry): { what: React.ReactNode; verb: string; panel: Panel } {
  switch (entry.kind) {
    case "run": return { what: <><span className="font-medium">{entry.run!.name}</span> <span className="text-fg-muted">· {entry.run!.status.toLowerCase()} at {entry.run!.steps.find((s) => s.state === "current")?.title ?? "end"}</span></>, verb: "Open run", panel: { kind: "run", id: entry.id } };
    case "decision": return { what: <><span className="font-medium">{entry.verdict!.kind}</span> <span className="text-fg-muted">→ {entry.verdict!.chosen}</span></>, verb: "Open decision", panel: { kind: "decision", id: entry.id } };
    case "change": {
      const f = entry.change!.fields;
      const only = f.length === 1 ? f[0] : undefined;
      return { what: <>{only ? <><span className="font-medium">{only.field}</span> <span className="text-fg-muted">{only.from} → </span><span className="font-medium">{only.to}</span></> : <><span className="font-medium">{f.length} fields</span> <span className="text-fg-muted">· {entry.change!.reason}</span></>}</>, verb: "History", panel: { kind: "history", id: entry.id } };
    }
    case "comment": return { what: <span className="line-clamp-2">{entry.text}</span>, verb: "Discussion", panel: { kind: "discussion" } };
    case "conversation": return { what: <span className="font-medium">{entry.text}</span>, verb: "Open conversation", panel: { kind: "conversation", id: entry.id } };
    default: return { what: <span>{entry.text}</span>, verb: entry.reference?.label ?? "Open", panel: { kind: "history", id: entry.id } };
  }
}

interface TimelineRailProps {
  entries: readonly Entry[];
  active: Panel | null;
  onOpen: (panel: Panel) => void;
  eligible: readonly Kind[];
}

function panelKey(p: Panel | null): string { return p ? `${p.kind}:${"id" in p ? p.id : ""}` : ""; }

function TimelineRail({ entries, active, onOpen, eligible }: TimelineRailProps): React.ReactElement {
  const [scope, setScope] = React.useState<Kind | "all">("all");
  const open = entries.filter((e) => e.needsMe).length;
  const sorted = [...entries].filter((e) => scope === "all" || e.kind === scope).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const groups = sorted.reduce<{ day: string; items: Entry[] }[]>((acc, e) => {
    const day = dayLabel(e.at);
    const last = acc[acc.length - 1];
    if (last && last.day === day) last.items.push(e); else acc.push({ day, items: [e] });
    return acc;
  }, []);
  return <div className="flex h-full min-h-0 flex-col bg-sheet">
    <header className="border-b border-border-subtle px-3 py-2">
      <div className="flex items-center gap-2">
        <h2 className="text-14 font-semibold">Timeline</h2>
        {open ? <CountBadge tone="warning" value={open} /> : null}
        <Button size="sm" variant={active?.kind === "discussion" ? "secondary" : "ghost"} className="ml-auto" onClick={() => onOpen({ kind: "discussion" })}>Discussion <span className="ml-1 text-fg-muted">{entries.filter((e) => e.kind === "comment").length}</span></Button>
      </div>
      <div className="mt-1 flex flex-wrap gap-1" role="group" aria-label="Scope">
        <Chip tone={scope === "all" ? "brand" : "muted"} variant={scope === "all" ? "solid" : "soft"} className="cursor-pointer" onClick={() => setScope("all")}>All</Chip>
        {eligible.map((k) => <Chip key={k} tone={scope === k ? "brand" : "muted"} variant={scope === k ? "solid" : "soft"} className="cursor-pointer" onClick={() => setScope(k)}>{KIND_META[k].label}</Chip>)}
      </div>
    </header>
    <ol className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
      {groups.map((group) => <li key={group.day}>
        <p className="sticky top-0 z-10 -mx-3 bg-sheet/95 px-3 py-1 text-11 font-medium uppercase tracking-wide text-fg-muted backdrop-blur">{group.day}</p>
        <ol className="relative ml-2.5 border-l border-border-subtle">
          {group.items.map((entry) => {
            const row = line(entry);
            const isActive = panelKey(row.panel) === panelKey(active);
            return <li key={entry.id} className={`relative -ml-px border-l-2 pl-7 pb-3 ${isActive ? "border-border-strong" : "border-transparent"}`}>
              <span className="absolute top-0.5 grid size-5 place-items-center rounded-full bg-sheet" style={{ left: "-0.6875rem" }}>{glyph(entry)}</span>
              <div className="flex items-baseline gap-2 text-11 text-fg-muted">
                <span>{time(entry.at)}</span>
                <span>{entry.actor.name}</span>
                {entry.needsMe ? <Badge tone="warning" variant="soft">needs you</Badge> : null}
              </div>
              <div className="text-13">{row.what}</div>
              <button type="button" aria-pressed={isActive} onClick={() => onOpen(row.panel)}
                className={`mt-0.5 text-12 hover:underline ${isActive ? "font-medium text-fg" : "text-link"}`}>{row.verb} →</button>
            </li>;
          })}
        </ol>
      </li>)}
      <li className="flex justify-center py-1"><Button size="sm" variant="ghost">Load older</Button></li>
    </ol>
  </div>;
}

/* ------------------------------------- the drill-ins -------------------------------------- */

function PanelFrame({ title, subtitle, onClose, back, children, footer }: { title: string; subtitle?: React.ReactNode; onClose: () => void; back?: boolean; children: React.ReactNode; footer?: React.ReactNode }): React.ReactElement {
  return <section className="flex h-full min-h-0 flex-col bg-sheet" aria-label={title}>
    <header className="flex items-start gap-2 border-b border-border-subtle px-3 py-2">
      {back ? <Button size="sm" variant="ghost" onClick={onClose} aria-label="Back to timeline">←</Button> : null}
      <div className="min-w-0 flex-1">
        <h2 className="truncate text-14 font-semibold">{title}</h2>
        {subtitle ? <div className="text-12 text-fg-muted">{subtitle}</div> : null}
      </div>
      {!back ? <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">✕</Button> : null}
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">{children}</div>
    {footer ? <footer className="shrink-0 border-t border-border-subtle p-3">{footer}</footer> : null}
  </section>;
}

function Composer({ placeholder }: { placeholder: string }): React.ReactElement {
  return <div className="rounded-6 border border-border-subtle bg-canvas p-2 text-12 text-fg-muted">{placeholder} <span className="float-right">⌘↵ send</span></div>;
}

/** The workflow's chatter: the stepper with the decision inline, then the run's own notes and comments. */
function RunPanel({ entry, onClose, back }: { entry: Entry; onClose: () => void; back?: boolean }): React.ReactElement {
  const run = entry.run!;
  return <PanelFrame title={run.name} subtitle={<>started {time(entry.at)} by {entry.actor.name} · <a href="#" className="text-link hover:underline">Open full run</a></>} onClose={onClose} back={back}
    footer={<Composer placeholder="Note on this run…" />}>
    <RunEntry entry={entry} dense />
    <h3 className="mt-4 mb-2 text-11 font-medium uppercase tracking-wide text-fg-muted">On this run</h3>
    <MessageFeed label="Run chatter" className="gap-3">
      <MessageRow avatar={<ActorAvatar actor={entry.actor} />} author={entry.actor.name} timestamp={run.steps[0]?.at ?? entry.at} channel={<Chip tone="muted" variant="soft">note</Chip>}>
        <span className="text-13">{run.steps[0]?.note}</span>
      </MessageRow>
      <MessageRow avatar={<ActorAvatar actor={{ initials: "WM", name: "Walker Member" }} />} author="Walker Member" timestamp={entry.at} channel={<Chip tone="brand" variant="soft">comment</Chip>}
        actions={<span className="flex gap-1"><Button size="sm" variant="ghost">Reply</Button><Button size="sm" variant="ghost">React</Button></span>}>
        <span className="text-13">Holding the contract step until Cloudflare answers on the rate.</span>
      </MessageRow>
    </MessageFeed>
  </PanelFrame>;
}

/** The record's history: every changeset as a step, the selected one expanded with its field diffs and Revert. */
function HistoryPanel({ entries, selected, onClose, back }: { entries: readonly Entry[]; selected: string; onClose: () => void; back?: boolean }): React.ReactElement {
  const [openId, setOpenId] = React.useState(selected);
  const changes = entries.filter((e) => e.kind === "change" || e.kind === "source").sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const items: StepListItem[] = changes.map((e) => ({
    id: e.id,
    state: e.id === openId ? "current" : "done",
    title: <button type="button" className="text-left hover:underline" onClick={() => setOpenId(e.id)}>{e.kind === "source" ? e.text : (e.change!.reason ?? `${e.change!.fields.length} fields`)}</button>,
    timestamp: e.at,
    detail: `${e.actor.name}${e.kind === "change" && e.change!.fields.length > 1 ? ` · ${e.change!.fields.length} fields` : ""}`,
    children: e.id === openId && e.kind === "change" ? <div className="mt-1">
      <table className="w-full text-12">
        <tbody>
          {e.change!.fields.map((f) => <tr key={f.field} className="align-top">
            <td className="w-24 py-0.5 pr-2 text-fg-muted">{f.field}</td>
            <td className="py-0.5 pr-2"><span className="text-fg-muted line-through">{f.from}</span> <span aria-hidden>→</span> <span className="font-medium">{f.to}</span></td>
            <td className="py-0.5 text-right">{f.source ? <Chip tone="muted" variant="soft">{f.source}</Chip> : null}</td>
          </tr>)}
        </tbody>
      </table>
      {e.change!.revertable ? <Button size="sm" variant="ghost" className="mt-1">Revert this changeset</Button> : null}
    </div> : undefined,
  }));
  return <PanelFrame title="History" subtitle={<>{changes.length} changesets · <a href="#" className="text-link hover:underline">Open history page</a></>} onClose={onClose} back={back}>
    <StepList items={items} />
  </PanelFrame>;
}

/** A related conversation: the thread's messages, reply in place. */
function ConversationPanel({ entry, onClose, back }: { entry: Entry; onClose: () => void; back?: boolean }): React.ReactElement {
  return <PanelFrame title={entry.text ?? "Conversation"} subtitle={<>{entry.reference?.meta} · <a href="#" className="text-link hover:underline">Open in inbox</a></>} onClose={onClose} back={back}
    footer={<Composer placeholder="Reply…" />}>
    <MessageFeed label="Conversation" className="gap-3">
      <MessageRow avatar={<ActorAvatar actor={{ initials: "CF", name: "billing@cloudflare.com" }} />} author="billing@cloudflare.com" timestamp={entry.at} channel={<Chip tone="muted" variant="soft">email</Chip>}>
        <span className="text-13">Please find attached invoice SYN-UBL-9506 for October.</span>
      </MessageRow>
      <MessageRow avatar={<ActorAvatar actor={entry.actor} />} author={entry.actor.name} timestamp={entry.at} channel={<Chip tone="muted" variant="soft">email</Chip>}>
        <span className="text-13">Line 1 is billed at 100.00 against an agreed 90.00. Could you issue a credit note?</span>
      </MessageRow>
    </MessageFeed>
  </PanelFrame>;
}

/** The record's own discussion: comments, notes, the composer and Follow. The chat aside, as one drill-in among others. */
function DiscussionPanel({ entries, onClose, back }: { entries: readonly Entry[]; onClose: () => void; back?: boolean }): React.ReactElement {
  const comments = entries.filter((e) => e.kind === "comment").sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return <PanelFrame title="Discussion" subtitle={<Button size="sm" variant="ghost" className="-ml-2">Following · changes, decisions</Button>} onClose={onClose} back={back}
    footer={<><div className="mb-2 flex items-center gap-2 text-12"><Chip tone="muted" variant="soft">Comment</Chip><Chip tone="muted" variant="outline">Note</Chip></div><Composer placeholder="Write a comment…" /></>}>
    <MessageFeed label="Discussion" className="gap-3">
      {comments.map((e) => <MessageRow key={e.id} avatar={<ActorAvatar actor={e.actor} />} author={e.actor.name} timestamp={e.at}
        actions={<span className="flex gap-1"><Button size="sm" variant="ghost">Reply</Button><Button size="sm" variant="ghost">React</Button><Button size="sm" variant="ghost">Star</Button></span>}>
        <span className="text-13">{e.text}</span>
        {e.attachments?.length ? <div className="mt-1 flex gap-1">{e.attachments.map((a) => <Chip key={a} tone="muted" variant="soft">{a}</Chip>)}</div> : null}
      </MessageRow>)}
    </MessageFeed>
  </PanelFrame>;
}

/** An answered decision: the card as it was answered, with the chosen alternative marked. */
function DecisionPanel({ entry, onClose, back }: { entry: Entry; onClose: () => void; back?: boolean }): React.ReactElement {
  const v = entry.verdict!;
  return <PanelFrame title={v.kind} subtitle={<>decided {time(entry.at)} by {entry.actor.name} as {v.byRole}</>} onClose={onClose} back={back}>
    <DecisionCardMock answered={v.chosen} card={{ title: v.kind, reason: "Answered on this record.", alternatives: [v.chosen, "Hold the bill", "Cancel the import"], mayAnswer: "admin, Walker Member" }} />
  </PanelFrame>;
}

function DrillIn({ panel, entries, onClose, back }: { panel: Panel; entries: readonly Entry[]; onClose: () => void; back?: boolean }): React.ReactElement | null {
  const byId = (id: string) => entries.find((e) => e.id === id)!;
  switch (panel.kind) {
    case "run": return <RunPanel entry={byId(panel.id)} onClose={onClose} back={back} />;
    case "history": return <HistoryPanel entries={entries} selected={panel.id} onClose={onClose} back={back} />;
    case "conversation": return <ConversationPanel entry={byId(panel.id)} onClose={onClose} back={back} />;
    case "discussion": return <DiscussionPanel entries={entries} onClose={onClose} back={back} />;
    case "decision": return <DecisionPanel entry={byId(panel.id)} onClose={onClose} back={back} />;
  }
}

/* --------------------------------------- the page ----------------------------------------- */

export interface TimelinePageProps {
  entries: readonly Entry[];
  eligible: readonly Kind[];
  /** The drill-in that starts open. */
  initial?: Panel | null;
  /** Narrow: the drill-in replaces the rail instead of sitting beside it. */
  narrow?: boolean;
}

export function TimelinePage({ entries, eligible, initial = null, narrow = false }: TimelinePageProps): React.ReactElement {
  const [panel, setPanel] = React.useState<Panel | null>(initial);
  const open = (next: Panel) => setPanel((cur) => (panelKey(cur) === panelKey(next) ? null : next));
  const rail = <div className="w-[20rem] shrink-0 border-l border-border-subtle"><TimelineRail entries={entries} eligible={eligible} active={panel} onOpen={open} /></div>;
  const drill = panel ? <div className={`${narrow ? "w-[20rem]" : "w-[26rem]"} shrink-0 border-l border-border-subtle`}><DrillIn panel={panel} entries={entries} onClose={() => setPanel(null)} back={narrow} /></div> : null;
  return <div className="flex h-[52rem] overflow-hidden rounded-8 border border-border-subtle bg-canvas">
    <div className="min-w-0 flex-1 p-5">
      <p className="text-11 uppercase tracking-wide text-fg-muted">Vendor bill</p>
      <h1 className="text-20 font-semibold">SYN-UBL-9506 · Cloudflare, Inc.</h1>
      <div className="mt-2 flex gap-2"><Badge tone="warning" variant="soft">Waiting · Match the supplier contract</Badge><Badge tone="neutral" variant="soft">€307.14</Badge></div>
      <div className="mt-6 h-[36rem] rounded-8 border border-dashed border-border-subtle p-4 text-12 text-fg-muted">record form (unchanged)</div>
    </div>
    {narrow ? (drill ?? rail) : <>{rail}{drill}</>}
  </div>;
}

const meta = {
  title: "Communication/Timeline + drill-in asides (whiteboard)",
  component: TimelinePage,
  excludeStories: ["TimelinePage"],
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof TimelinePage>;

export default meta;
type Story = StoryObj<typeof meta>;

const eligible: readonly Kind[] = ["comment", "change", "run", "decision", "conversation", "source", "task"];

/** The rail alone: one line per event, a verb per line, nothing expanded. */
export const Rail: Story = { args: { entries: billEntries, eligible } };

/** "Open run" opened the workflow's own aside: stepper with the decision inline, the run's notes, a composer for the run. */
export const RunOpen: Story = { args: { entries: billEntries, eligible, initial: { kind: "run", id: "run-import" } } };

/** "History" opened the record's history aside: every changeset, the clicked one expanded with its diffs and Revert. */
export const HistoryOpen: Story = { args: { entries: billEntries, eligible, initial: { kind: "history", id: "chg-supplier" } } };

/** "Discussion" is the old chat aside, now one drill-in among the others. */
export const DiscussionOpen: Story = { args: { entries: billEntries, eligible, initial: { kind: "discussion" } } };

/** "Open conversation" shows the related email thread with reply in place. */
export const ConversationOpen: Story = { args: { entries: billEntries, eligible, initial: { kind: "conversation", id: "conv-1" } } };

/** Narrow viewport: the drill-in replaces the rail and carries a back verb. */
export const NarrowRunOpen: Story = { args: { entries: billEntries, eligible, narrow: true, initial: { kind: "run", id: "run-import" } } };
