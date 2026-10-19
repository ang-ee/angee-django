import * as React from "react";
import { Avatar, Badge, Button, Chip, CountBadge, Glyph, StatusIcon, StepList, Tabs, type StepListItem } from "@angee/ui";
import { DecisionCardMock, billEntries, time } from "../activity-explorer.mocks";
import { PaneToolbar, ScopeRow } from "./PaneToolbar";
import { KIND_PRESENTATION, type AsideData, type AsideTab, type Message, type RelatedGroup, type TaskItem, type TimelineLine } from "./types";

/**
 * The right region: what is around the record. Four tabs, each present only where the record
 * can carry it; one toolbar per pane; drill-ins replace the pane with a way back. Every pane
 * shares the line anatomy, the empty state and the gutter.
 */

const dayLabel = (iso: string) => {
  const d = new Date(iso); const diff = Math.round((new Date(2026, 9, 19).getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86_400_000);
  return diff === 0 ? "Today" : diff === 1 ? "Yesterday" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

const DaySeparator = ({ label }: { label: string }) => <li className="px-1 pb-1 pt-4 text-12 font-medium text-fg-muted first:pt-1">{label}</li>;
const Empty = ({ text, verb }: { text: string; verb?: string }) => <div className="m-3 rounded-8 border border-dashed border-border-subtle p-4 text-center text-13 text-fg-muted">{text}{verb ? <div className="mt-2"><Button size="sm" variant="secondary">{verb}</Button></div> : null}</div>;

/* Timeline ------------------------------------------------------------------------------- */

export function TimelineLineRow({ line }: { line: TimelineLine }): React.ReactElement {
  const kind = KIND_PRESENTATION[line.kind];
  return (
    <li>
      <a href="#" title={kind.verb} className="group/line flex gap-2 rounded-6 px-1 py-1.5 hover:bg-canvas/70">
        <span className="relative mt-0.5 shrink-0"><StatusIcon tone={kind.tone} icon={kind.icon} size="sm" />{line.needsMe ? <span className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-warning-text ring-2 ring-sheet" /> : null}</span>
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="min-w-0 flex-1 truncate text-13"><span className={line.needsMe ? "font-semibold" : "font-medium"}>{line.title}</span>{line.detail ? <span className="text-fg-muted"> · {line.detail}</span> : null}</span>
            <span className="shrink-0 text-12 tabular-nums text-fg-muted">{time(line.at)}</span>
          </span>
          <span className="flex items-center gap-1 text-12 text-fg-muted">
            <span className="truncate">{line.actor}{line.folded ? ` · ${line.folded}` : ""}</span>
            <span className="ml-auto flex shrink-0 items-center opacity-0 group-hover/line:opacity-100"><span className="text-link">{kind.verb}</span><Glyph name="chevron-right" /></span>
          </span>
        </span>
      </a>
    </li>
  );
}

export function TimelinePane({ data }: { data: AsideData }): React.ReactElement {
  const counts = Object.fromEntries(data.kinds.map((k) => [k, data.lines.filter((l) => l.kind === k).length]));
  const needs = data.lines.filter((l) => l.needsMe).length;
  const sorted = [...data.lines].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const items = data.kinds.map((k) => ({ id: k, label: KIND_PRESENTATION[k].label, count: counts[k] }));
  return (
    <>
      <PaneToolbar scope={<ScopeRow active="all" max={2} items={[{ id: "all", label: "All", count: data.lines.length }, ...(needs ? [{ id: "needs", label: "Needs you", count: needs, tone: "warning" as const }] : []), ...items]} />} />
      {sorted.length === 0 ? <Empty text="Nothing has happened on this record yet." /> : (
        <ul className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
          {sorted.map((line, i) => {
            const day = dayLabel(line.at); const prev = i ? dayLabel(sorted[i - 1]!.at) : null;
            return <React.Fragment key={line.id}>{day !== prev ? <DaySeparator label={day} /> : null}<TimelineLineRow line={line} /></React.Fragment>;
          })}
          <li className="py-2 text-center"><Button size="sm" variant="ghost">Load older</Button></li>
        </ul>
      )}
    </>
  );
}

/* Discussion ----------------------------------------------------------------------------- */

export function DiscussionPane({ messages }: { messages: readonly Message[] }): React.ReactElement {
  const n = (k?: Message["kind"]) => messages.filter((m) => m.kind === k).length;
  return (
    <>
      <PaneToolbar scope={<ScopeRow active="all" items={[{ id: "all", label: "All", count: messages.length }, { id: "comment", label: "Comments", count: n() }, { id: "note", label: "Notes", count: n("note") }, { id: "email", label: "Emails", count: n("email") }]} />} actions={[{ icon: "check", label: "Mark read" }]} />
      {messages.length === 0 ? <Empty text="No one has written on this record yet." verb="Write the first comment" /> : (
        <ul className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          {messages.map((m, i) => (
            <li key={i} className="flex gap-2 py-2">
              <Avatar size="sm" initials={m.who.split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()} />
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 text-12 text-fg-muted"><span className="text-13 font-medium text-fg">{m.who}</span><span className="tabular-nums">{time(m.at)}</span>{m.kind ? <Chip size="sm" tone={m.kind === "note" ? "warning" : "muted"} variant="soft">{m.kind === "note" ? "Internal note" : "Email"}</Chip> : null}</div>
                <p className="text-13 leading-5">{m.text}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <footer className="shrink-0 border-t border-border-subtle p-3">
        <div className="mb-2 flex items-center gap-1 text-12">
          <Chip size="sm" tone="brand" variant="solid">Comment</Chip><Chip size="sm" tone="muted" variant="soft">Note</Chip><Chip size="sm" tone="muted" variant="soft">Email</Chip>
          <span className="ml-auto text-12 text-fg-muted">To everyone with access</span>
        </div>
        <div className="flex items-end gap-2 rounded-6 border border-border-subtle bg-canvas p-2 text-13 text-fg-muted"><span className="flex-1">Write a comment…</span><Glyph name="attachment" /><Glyph name="arrow-up" /></div>
      </footer>
    </>
  );
}

/* Tasks ---------------------------------------------------------------------------------- */

export function TasksPane({ tasks }: { tasks: readonly TaskItem[] }): React.ReactElement {
  const open = tasks.filter((x) => !x.done); const done = tasks.filter((x) => x.done);
  return (
    <>
      <PaneToolbar scope={<ScopeRow active="open" items={[{ id: "open", label: "Open", count: open.length }, { id: "done", label: "Done", count: done.length }]} />} actions={[{ icon: "plus", label: "Schedule" }]} />
      {open.length === 0 ? <Empty text="Nothing planned on this record." verb="Schedule the first task" /> : (
        <ul className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
          {open.map((x) => (
            <li key={x.title}><a href="#" className="flex gap-2 rounded-6 px-1 py-1.5 hover:bg-canvas/70"><span className="mt-0.5 grid size-4 shrink-0 place-items-center rounded-4 border border-border-strong" /><span className="min-w-0 flex-1"><span className="block truncate text-13 font-medium">{x.title}</span><span className="text-12 text-fg-muted">due {x.due} · {x.who}</span></span></a></li>
          ))}
          {done.length ? <li className="px-1 pt-3 text-12 text-fg-muted"><Glyph name="chevron-right" /> Completed · {done.length}</li> : null}
        </ul>
      )}
    </>
  );
}

/* Related -------------------------------------------------------------------------------- */

export function RelatedPane({ groups }: { groups: readonly RelatedGroup[] }): React.ReactElement {
  return (
    <>
      <PaneToolbar scope={<ScopeRow active={groups[0]?.id ?? ""} items={groups.map((g) => ({ id: g.id, label: g.label, count: g.count }))} />} />
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {groups.map((g) => (
          <section key={g.id} className="py-2">
            <div className="flex items-center gap-1 px-1 text-12 font-medium text-fg-muted">
              <span>{g.label}</span><span className="tabular-nums">{g.count}</span>
              {g.verb ? <button type="button" className="ml-auto flex items-center gap-0.5 rounded-6 px-1 text-12 text-link hover:bg-canvas"><Glyph name="plus" />{g.verb}</button> : null}
            </div>
            {g.rows.length ? <ul>{g.rows.map((r) => <li key={r.label}><a href="#" className="flex items-baseline gap-2 rounded-6 px-1 py-1 hover:bg-canvas/70"><span className="min-w-0 flex-1 truncate text-13">{r.label}</span><span className="shrink-0 text-12 text-fg-muted">{r.meta}</span></a></li>)}</ul>
              : <p className="px-1 py-1 text-13 text-fg-muted">None yet.</p>}
          </section>
        ))}
      </div>
    </>
  );
}

/* Drill-in --------------------------------------------------------------------------------- */

export function RunDrillIn({ onBack }: { onBack?: () => void }): React.ReactElement {
  const run = billEntries[0]!;
  return (
    <>
      <PaneToolbar back={{ label: "Timeline", onClick: onBack }} title={<span className="flex items-center gap-2">Bill import <Badge tone="warning" variant="soft" className="text-12">Waiting</Badge></span>} search={false}
        actions={[{ icon: "arrow-up-right", label: "Open run page" }, { icon: "x", label: "Stop", tone: "danger", text: true }]} />
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
        <p className="mb-2 text-12 text-fg-muted">started {time(run.at)} by Bill import · on this bill · 9 of 13 steps done</p>
        <StepList items={run.run!.steps.map((step): StepListItem => ({
          id: step.id, state: step.state, title: step.id.endsWith("done") ? `${step.title} · show` : step.title, timestamp: null,
          detail: step.id.endsWith("done") ? undefined : step.note, children: step.decision ? <DecisionCardMock card={step.decision} /> : undefined,
        }))} />
      </div>
    </>
  );
}

/* The aside --------------------------------------------------------------------------------- */

export function ContextAside({ data, tab = "timeline", drillIn }: { data: AsideData; tab?: AsideTab; drillIn?: "run" }): React.ReactElement {
  const needs = data.lines.filter((l) => l.needsMe).length;
  const tabs: { id: AsideTab; label: string; icon: string; count?: number; tone?: "warning" }[] = [
    { id: "timeline", label: "Timeline", icon: "list-ordered", count: needs || undefined, tone: "warning" },
    ...(data.discussion ? [{ id: "discussion" as const, label: "Discussion", icon: "comments" }] : []),
    ...(data.tasks ? [{ id: "tasks" as const, label: "Tasks", icon: "check", count: data.tasks.filter((x) => !x.done).length || undefined }] : []),
    ...(data.related ? [{ id: "related" as const, label: "Related", icon: "link", count: data.related.reduce((n, g) => n + g.count, 0) || undefined }] : []),
  ];
  return (
    <aside className="flex w-[26rem] shrink-0 flex-col border-l border-border-subtle bg-sheet">
      <Tabs value={tab} variant="card" className="flex min-h-0 flex-1 flex-col">
        <Tabs.List className="flex shrink-0 px-2 pt-2">
          {tabs.map((x) => <Tabs.Tab key={x.id} value={x.id} className="h-8 min-w-fit flex-1 px-1.5 text-13 font-medium" icon={<Glyph name={x.icon} />}>{x.label}{x.count ? <CountBadge tone={x.tone ?? "neutral"} value={x.count} className="ml-1" /> : null}</Tabs.Tab>)}
        </Tabs.List>
        <div className="flex min-h-0 flex-1 flex-col">
          {drillIn === "run" ? <RunDrillIn />
            : tab === "timeline" ? <TimelinePane data={data} />
            : tab === "discussion" && data.discussion ? <DiscussionPane messages={data.discussion.messages} />
            : tab === "tasks" && data.tasks ? <TasksPane tasks={data.tasks} />
            : data.related ? <RelatedPane groups={data.related} /> : null}
        </div>
      </Tabs>
    </aside>
  );
}

/** The tools edge: session-scoped, record-independent (agents, logs). */
export function ToolsEdge(): React.ReactElement {
  return <div className="flex w-9 shrink-0 flex-col items-center gap-2 border-l border-border-subtle bg-sheet py-2 text-fg-muted"><button type="button" aria-label="Agents" className="grid size-7 place-items-center rounded-6 hover:bg-canvas"><Glyph name="agent" /></button><button type="button" aria-label="Logs" className="grid size-7 place-items-center rounded-6 hover:bg-canvas"><Glyph name="terminal" /></button></div>;
}
