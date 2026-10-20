import type { Meta, StoryObj } from "@storybook/react-vite";
import * as React from "react";
import { Badge, Button, Chip, CountBadge, EmptyState, MessageFeed, MessageRow } from "@angee/ui";
import { ActorAvatar, EntryBody, KIND_META, KindTag, billEntries, dayLabel, fileEntries, partyEntries, titleOf, type Entry, type Kind } from "./activity-explorer.mocks";

/**
 * Whiteboard prototype (2026-10-19): one Activity explorer for every page type.
 *
 * Not wired to sources. It draws the shape the design discussion is about:
 * - one chronology per record, grouped by day, newest first;
 * - a workflow run is ONE entry that renders its stepper (done steps folded, the waiting
 *   step with its decision card inline, upcoming steps planned), never N flat rows;
 * - a change is a field diff with its source; a decision answered is a verdict; a comment is
 *   a message with its verbs; a conversation and a source event are references;
 * - scope chips come from what the record CAN have (eligibility), each with a count;
 * - actor and "needs me" filters, search, density;
 * - the same explorer on a bill, a file and a party, with their own eligible scopes.
 */

export interface ActivityExplorerProps {
  entries: readonly Entry[];
  /** What this record CAN carry; chips are drawn from here, with counts. */
  eligible: readonly Kind[];
  title?: string;
  dense?: boolean;
  initialKind?: Kind | "all";
  composer?: boolean;
}

export function ActivityExplorer({ entries, eligible, title = "Activity", dense = false, initialKind = "all", composer = true }: ActivityExplorerProps): React.ReactElement {
  const [kind, setKind] = React.useState<Kind | "all">(initialKind);
  const [needsMe, setNeedsMe] = React.useState(false);
  const [actor, setActor] = React.useState<string | "all">("all");
  const [query, setQuery] = React.useState("");
  const [isDense, setDense] = React.useState(dense);
  const actors = React.useMemo(() => [...new Map(entries.map((e) => [e.actor.name, e.actor])).values()], [entries]);
  const counts = React.useMemo(() => Object.fromEntries(eligible.map((k) => [k, entries.filter((e) => e.kind === k).length])) as Record<Kind, number>, [eligible, entries]);
  const filtered = entries
    .filter((e) => kind === "all" || e.kind === kind)
    .filter((e) => !needsMe || e.needsMe)
    .filter((e) => actor === "all" || e.actor.name === actor)
    .filter((e) => !query || JSON.stringify(e).toLowerCase().includes(query.toLowerCase()))
    .sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const groups = filtered.reduce<{ day: string; items: Entry[] }[]>((acc, e) => {
    const day = dayLabel(e.at);
    const last = acc[acc.length - 1];
    if (last && last.day === day) last.items.push(e); else acc.push({ day, items: [e] });
    return acc;
  }, []);
  const open = entries.filter((e) => e.needsMe).length;

  return <div className="flex h-full min-h-0 flex-col bg-sheet">
    <header className="border-b border-border-subtle px-3 pt-3">
      <div className="flex items-center gap-2">
        <h2 className="text-14 font-semibold">{title}</h2>
        {open ? <CountBadge tone="warning" value={open} /> : null}
        <span className="ml-auto flex items-center gap-1">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search activity" aria-label="Search activity"
            className="h-7 w-36 rounded-6 border border-border-subtle bg-canvas px-2 text-12 outline-none focus:border-border-strong" />
          <Button size="sm" variant={isDense ? "secondary" : "ghost"} aria-pressed={isDense} onClick={() => setDense((v) => !v)}>Dense</Button>
        </span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1 pb-2" role="group" aria-label="Scope">
        <Button size="sm" variant={kind === "all" ? "secondary" : "ghost"} aria-pressed={kind === "all"} onClick={() => setKind("all")}>All <span className="ml-1 text-fg-muted">{entries.length}</span></Button>
        {eligible.map((k) => <Button key={k} size="sm" variant={kind === k ? "secondary" : "ghost"} aria-pressed={kind === k} disabled={counts[k] === 0} onClick={() => setKind(k)}>
          {KIND_META[k].label} <span className="ml-1 text-fg-muted">{counts[k]}</span>
        </Button>)}
        <span className="mx-1 h-4 w-px bg-border-subtle" aria-hidden />
        <Button size="sm" variant={needsMe ? "secondary" : "ghost"} aria-pressed={needsMe} onClick={() => setNeedsMe((v) => !v)}>Needs me {open ? <CountBadge tone="warning" value={open} className="ml-1" /> : null}</Button>
        <select aria-label="Actor" value={actor} onChange={(e) => setActor(e.target.value)} className="h-7 rounded-6 border border-border-subtle bg-canvas px-1 text-12">
          <option value="all">Anyone</option>
          {actors.map((a) => <option key={a.name} value={a.name}>{a.name}</option>)}
        </select>
      </div>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
      {filtered.length === 0 ? <EmptyState icon="activity" title="Nothing here" description="Nothing matches this scope on this record." className="min-h-40 p-4" /> : null}
      {groups.map((group) => <section key={group.day} className="mb-3">
        <h3 className="sticky top-0 z-10 -mx-3 bg-sheet/95 px-3 py-1 text-11 font-medium uppercase tracking-wide text-fg-muted backdrop-blur">{group.day}</h3>
        <MessageFeed label={group.day} className={isDense ? "gap-2" : "gap-4"}>
          {group.items.map((entry) => <MessageRow key={entry.id} avatar={<ActorAvatar actor={entry.actor} />} author={entry.actor.name}
            timestamp={entry.at} channel={<KindTag kind={entry.kind} />}
            meta={entry.needsMe ? <Badge tone="warning" variant="soft">needs you</Badge> : null}
            actions={entry.kind === "comment" ? <span className="flex gap-1"><Button size="sm" variant="ghost">Reply</Button><Button size="sm" variant="ghost">React</Button><Button size="sm" variant="ghost">Star</Button></span> : null}>
            {entry.kind !== "run" && !isDense ? <span className="text-13 font-medium">{titleOf(entry)}</span> : null}
            <div className={entry.kind !== "run" && !isDense ? "mt-1" : ""}><EntryBody entry={entry} dense={isDense} /></div>
          </MessageRow>)}
        </MessageFeed>
      </section>)}
      <div className="flex justify-center py-2"><Button size="sm" variant="ghost">Load older</Button></div>
    </div>
    {composer ? <footer className="shrink-0 border-t border-border-subtle p-3">
      <div className="mb-2 flex items-center gap-2 text-12 text-fg-muted">
        <Chip tone="muted" variant="soft">Comment</Chip><Chip tone="muted" variant="outline">Note</Chip>
        <Button size="sm" variant="ghost" className="ml-auto">Following · changes, decisions</Button>
      </div>
      <div className="rounded-6 border border-border-subtle bg-canvas p-2 text-12 text-fg-muted">Write a comment… <span className="float-right">⌘↵ send</span></div>
    </footer> : null}
  </div>;
}

/* ------------------------------------------------------------------------------------------ */

const meta = {
  title: "Explorations/2026-10-19 Activity explorer — context aside ruling",
  component: ActivityExplorer,
  excludeStories: ["ActivityExplorer"],
  parameters: { layout: "padded" },
} satisfies Meta<typeof ActivityExplorer>;

export default meta;
type Story = StoryObj<typeof meta>;

const frame = (node: React.ReactNode) => <div className="h-[52rem] w-[26rem] overflow-hidden rounded-8 border border-border-subtle">{node}</div>;

/** A vendor bill: the import run is one entry with its stepper, decisions inline; everything else chronological. */
export const Bill: Story = {
  args: { entries: billEntries, eligible: ["comment", "change", "run", "decision", "conversation", "source", "task"], title: "Activity" },
  render: (args) => frame(<ActivityExplorer {...args} />),
};

/** Same explorer on a storage File: no comments or tasks are eligible, so those scopes are absent, not empty. */
export const File: Story = {
  args: { entries: fileEntries, eligible: ["change", "run", "source", "conversation"], title: "Activity", composer: false },
  render: (args) => frame(<ActivityExplorer {...args} />),
};

/** Same explorer on a Party: the nexus timeline, files and tasks fold in as scopes instead of extra tabs. */
export const Party: Story = {
  args: { entries: partyEntries, eligible: ["change", "decision", "conversation", "source", "file", "task"], title: "Activity", composer: false },
  render: (args) => frame(<ActivityExplorer {...args} />),
};

/** Scoped to decisions: the empty state names the scope, the chips keep their counts. */
export const ScopedToRuns: Story = {
  args: { entries: billEntries, eligible: ["comment", "change", "run", "decision", "conversation", "source", "task"], initialKind: "run" },
  render: (args) => frame(<ActivityExplorer {...args} />),
};

/** Dense: one line per entry, runs keep their stepper. */
export const Dense: Story = {
  args: { entries: billEntries, eligible: ["comment", "change", "run", "decision", "conversation", "source", "task"], dense: true },
  render: (args) => frame(<ActivityExplorer {...args} />),
};
