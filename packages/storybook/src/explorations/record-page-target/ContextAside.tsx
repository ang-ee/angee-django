import { Fragment, useMemo, useState, type ComponentType } from "react";
import {
  Alert, AppRuntimeProvider, Avatar, Button, CHATTER_CONTAINERS, Chip, EmptyState,
  ErrorBanner, Glyph, Input, MessageComposer, MessageFeed, MessageRow, PaneLabel,
  PaneLabelPopover, PaneRow, PaneRows, PaneToolbar, PaneToolbarHost, PaneToolbarMenu,
  Popover, RelatedPane, ScopeChip, Tabs, Textarea, cn, containersFromChildren,
  statusTone, textRoleVariants, useChatter, type ChatterViewContext, type RelatedGroupContent,
} from "@angee/ui";
import { RuntimeFixture, jsonResponse } from "../../stories/runtime-fixtures";
import {
  bill, decision, discussion, needsAttention, related, runSteps, tasks, timelineKinds, timelines,
  type Activity, type PageKind,
} from "./fixtures";
import { ChoiceMenu, Spec } from "./spec";

// Vite already discovers addon source for this composed-host Storybook. Load the
// actual addon owner without adding a package dependency or importing generated
// project schema into this schema-independent package's TypeScript graph.
const decisionOwners = import.meta.glob<{
  DecisionCard: ComponentType<{ decision: typeof decision; compact?: boolean; inStep?: { records: readonly string[] } }>;
}>("../../../../../addons/angee/decisions/web/src/DecisionCard.tsx", { eager: true });
const DecisionCard = Object.values(decisionOwners)[0]?.DecisionCard;

export function ContextAside({ page }: { page: PageKind }) {
  const { activeTab, setActiveTab } = useChatter();
  const run = activeTab === "run";
  const tab = run ? "timeline" : activeTab;
  const hasDiscussion = page === "bill" || page === "knowledge";
  const tabs = [
    { id: "timeline", label: "Timeline", count: needsAttention(page) || undefined },
    ...(hasDiscussion ? [{ id: "discussion", label: "Discussion", count: undefined }] : []),
    ...(page === "bill" || page === "knowledge" ? [{ id: "tasks", label: "Tasks", count: tasks[page].length }] : []),
    { id: "related", label: "Related", count: page === "bill" ? 5 : related[page].reduce((sum, group) => sum + group.rows.length, 0) },
  ];
  return <aside aria-label="Context aside" className="target-aside" data-region="context-aside">
    <Tabs value={tab} onValueChange={(value) => setActiveTab(value)} variant="card" className="flex min-h-0 flex-1 flex-col">
      <Spec owner="Chatter · fixed tabs" className="target-aside-head">
        <Tabs.List className="flex h-10 justify-start px-2">
          {tabs.map((item) => <Tabs.Tab key={item.id} value={item.id} className="h-10 flex-none px-2">
            {item.label}{item.count !== undefined ? <Chip size="micro" tone={item.id === "timeline" ? statusTone("waiting") : "neutral"} variant="soft" className="ml-1">{item.count}</Chip> : null}
          </Tabs.Tab>)}
        </Tabs.List>
      </Spec>
      <Tabs.Panel value="timeline" className="flex min-h-0 flex-1 flex-col pt-0">
        {run ? <RunDrillIn /> : <Timeline page={page} />}
      </Tabs.Panel>
      {hasDiscussion ? <Tabs.Panel value="discussion" className="flex min-h-0 flex-1 flex-col pt-0"><Discussion /></Tabs.Panel> : null}
      {page === "bill" || page === "knowledge" ? <Tabs.Panel value="tasks" className="flex min-h-0 flex-1 flex-col pt-0"><Tasks page={page} /></Tabs.Panel> : null}
      <Tabs.Panel value="related" className="flex min-h-0 flex-1 flex-col pt-0"><Related page={page} /></Tabs.Panel>
    </Tabs>
  </aside>;
}

function Timeline({ page }: { page: PageKind }) {
  const [scope, setScope] = useState("all");
  const [search, setSearch] = useState(false);
  const [query, setQuery] = useState("");
  const { setActiveTab } = useChatter();
  const entries = timelines[page];
  const filtered = entries.filter((entry) => (scope === "all" || scope === "needs" && entry.needsMe || entry.kind === scope)
    && `${entry.title} ${entry.detail ?? ""} ${entry.actor}`.toLowerCase().includes(query.toLowerCase()));
  const open = (entry: Activity) => {
    if (entry.kind === "Runs" || entry.kind === "Decisions") setActiveTab("run", { from: "timeline", target: decision.id });
    else if (entry.kind === "Comments" && page === "bill") setActiveTab("discussion");
    else if (entry.kind === "Tasks") setActiveTab("tasks");
    else setActiveTab("related");
  };
  return <>
    <Spec owner="TimelineRail · scope menu" className="shrink-0">
    <PaneToolbarHost className="h-auto flex-none">
    <PaneToolbar scope={<div className="flex min-w-0 items-center gap-1">
      <ScopeChip label="All" count={entries.length} active={scope === "all"} onClick={() => setScope("all")} />
      {needsAttention(page) ? <ScopeChip label="Needs you" count={needsAttention(page)} active={scope === "needs"} tone="warning" onClick={() => setScope("needs")} /> : null}
      <ChoiceMenu label="More timeline scopes" value={scope} onChange={setScope}
        items={timelineKinds.map((kind) => ({ value: kind, label: `${kind} · ${entries.filter((entry) => entry.kind === kind).length}` }))}>
        <span className={cn(textRoleVariants({ role: "caption" }), "inline-flex items-center gap-1")}><Glyph name="filter" />9 more</span>
      </ChoiceMenu>
    </div>}
      search onSearch={() => setSearch(!search)}
      overflow={<PaneToolbarMenu label="Timeline options" items={[{ icon: "check", label: "Clear filters", onClick: () => { setScope("all"); setQuery(""); } }]} />} />
    </PaneToolbarHost>
    </Spec>
    {search ? <div className="p-2"><Input aria-label="Search timeline" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search this timeline…" /></div> : null}
    <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
      <PaneRows>{filtered.map((entry, index) => <Fragment key={entry.id}>
        {entry.day !== filtered[index - 1]?.day ? <PaneLabel>{entry.day}</PaneLabel> : null}
        <li><PaneRow icon={entry.icon} tone={entry.tone} title={entry.title} detail={entry.detail}
          meta={<time dateTime={entry.at}>{entry.time}</time>}
          sub={`${entry.actor}${entry.folded ? ` · ${entry.folded}` : ""}`} needsMe={entry.needsMe}
          verb={entry.kind === "Runs" ? "Open run" : "Open"} onOpen={() => open(entry)}
          label={[entry.title, entry.detail, entry.actor, entry.folded].filter(Boolean).join(" · ")} /></li>
      </Fragment>)}</PaneRows>
      {!filtered.length ? <EmptyState icon="search" title="No matching activity" description="Try another scope or clear your search." className="min-h-40 p-4" /> : null}
    </div>
  </>;
}

function RunDrillIn() {
  const { setActiveTab } = useChatter();
  const [stopped, setStopped] = useState(false);
  return <Spec owner="Run drill-in · PaneRow steps" className="flex min-h-0 flex-1 flex-col">
    <PaneToolbarHost back={{ label: "Timeline", onClick: () => setActiveTab("timeline", {}) }} title="Bill import">
      <PaneToolbar scope={<Chip size="sm" tone={statusTone(stopped ? "stopped" : "waiting")} variant="soft">{stopped ? "Stopped" : "Waiting"}</Chip>}
        actions={[{ icon: "circle-x", label: "Stop", text: true, tone: "danger", disabled: stopped, onClick: () => setStopped(true) }]} />
      <div className="min-h-0 flex-1 overflow-auto px-3 pb-5 pt-3">
        <PaneRows>
          <PaneLabel>Oct 20, 2026</PaneLabel>
          {runSteps.map((step) => <Fragment key={step.title}>
            <li><PaneRow icon={step.state === "waiting" ? "help" : step.state === "started" ? "activity" : "check"}
              tone={step.state === "waiting" ? "warning" : step.state === "started" ? "info" : "success"}
              title={step.title} sub={step.detail} meta={step.time} needsMe={step.state === "waiting"}
              label={`${step.title} · ${step.detail}`} /></li>
            {step.warning ? <li className="my-2 ml-6"><Alert tone="warning" variant="soft" className="p-3">{step.warning}</Alert></li> : null}
          </Fragment>)}
        </PaneRows>
        <div className="ml-6 mt-3">{stopped ? <EmptyState title="Run stopped" description="No further steps will run in this preview." icon="circle-x" className="min-h-32 p-3" /> : <DecisionFixture />}</div>
      </div>
    </PaneToolbarHost>
  </Spec>;
}

function DecisionFixture() {
  const [current, setCurrent] = useState(decision);
  const schemas = useMemo(() => {
    const fetch = async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (!init?.body) return jsonResponse({ token: "storybook" });
      setCurrent((value) => ({ ...value, is_open: false, verdict_label: "Confirmed" }));
      return jsonResponse({ data: { decide: { ok: true, message: "", id: decision.id, code: "", validation_errors: null } } });
    };
    return { public: { url: "/target-fixture/public", fetch }, console: { url: "/target-fixture/console", fetch } };
  }, []);
  if (!DecisionCard) return <ErrorBanner title="DecisionCard owner unavailable" description="The composed Storybook must resolve the shipped Decisions addon." />;
  return <RuntimeFixture schemas={schemas} activeSchema="console"><DecisionCard decision={current} compact inStep={{ records: [] }} /></RuntimeFixture>;
}

const RELATED_CONTEXT: ChatterViewContext = {
  pathname: "/target/record", params: {}, view: { kind: "record", type: "target/record", sqid: bill.number },
};
function Related({ page }: { page: PageKind }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState(false);
  const { setActiveTab } = useChatter();
  const containers = useMemo(() => containersFromChildren(CHATTER_CONTAINERS, {
    "related#groups": Object.fromEntries(related[page].map((group, sequence) => [group.id, { sequence, content: {
      label: group.label, useCount: () => group.rows.length, useVerb: () => Boolean(group.verb),
      render: (_context, label) => <>
        {label(group.verb ? <PaneLabelPopover icon={group.verb === "Odoo" ? "arrow-up-right" : "plus"} label={group.verb}>
          <p className={textRoleVariants({ role: "description" })}>{group.verb === "Odoo" ? "Odoo · us.apexive.com" : `Choose a ${group.id === "files" ? "file to attach" : "page to bind"}`}</p>
          <PaneRow title={group.rows[0]!.title} icon={group.rows[0]!.icon} />
          <Popover.Close render={<Button size="sm" variant="secondary">Done</Button>} />
        </PaneLabelPopover> : null)}
        <li><PaneRows>{group.rows.filter((row) => `${row.title} ${row.detail}`.toLowerCase().includes(query.toLowerCase())).map((row) => <li key={row.title}>
          <PaneRow title={row.title} sub={row.detail} icon={row.icon} onOpen={group.id === "runs" ? () => setActiveTab("run", { from: "related", target: decision.id }) : undefined} label={`${row.title} · ${row.detail}`} />
        </li>)}</PaneRows></li>
      </>,
    } satisfies RelatedGroupContent }])),
  }), [page, query, setActiveTab]);
  return <PaneToolbarHost>
    <PaneToolbar scope={<ScopeChip label={page === "nexus" ? "Selected sender" : "This record"} active onClick={() => setQuery("")} />}
      search onSearch={() => setSearch(!search)} overflow={<PaneToolbarMenu label="Related options" items={[{ label: "Clear search", icon: "search", onClick: () => setQuery("") }]} />} />
    {search ? <div className="p-2"><Input aria-label="Search related" placeholder="Find a related item…" value={query} onChange={(event) => setQuery(event.target.value)} /></div> : null}
    <AppRuntimeProvider runtime={{ containers }}><RelatedPane context={RELATED_CONTEXT} /></AppRuntimeProvider>
  </PaneToolbarHost>;
}

function Discussion() {
  const [text, setText] = useState("");
  const [mode, setMode] = useState("Comment");
  const [messages, setMessages] = useState(discussion);
  return <PaneToolbarHost>
    <PaneToolbar scope={<ScopeChip label="All" count={messages.length} active onClick={() => undefined} />}
      overflow={<PaneToolbarMenu label="Discussion options" items={[{ icon: "check", label: "Mark read" }]} />} />
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
      <MessageFeed label="Discussion">
        <PaneLabel>Oct 19, 2026</PaneLabel>
        {messages.map((entry) => <MessageRow key={entry.id} author={entry.author} avatar={<Avatar size="sm" initials={entry.initials} />}
          meta={entry.time} channel={entry.note ? <Chip size="micro" tone="warning" variant="soft">Internal note</Chip> : undefined}>
          {entry.text}
        </MessageRow>)}
      </MessageFeed>
    </div>
    <div className="shrink-0 border-t border-border-subtle p-3">
      <div className="mb-3 flex gap-1">{["Comment", "Note", "Email"].map((label) => <ScopeChip key={label} label={label} active={mode === label} onClick={() => setMode(label)} />)}</div>
      <MessageComposer input={<Textarea aria-label="Write a comment" placeholder={`Write a ${mode.toLowerCase()}…`} rows={3} resize="none" value={text} onChange={(event) => setText(event.target.value)} />}
        hint={<span className={textRoleVariants({ role: "caption" })}>{mode === "Note" ? "Visible to the AP team" : "To everyone with access"}</span>}
        actions={<Button size="sm" disabled={!text.trim()} onClick={() => {
          setMessages([...messages, { id: `draft-${messages.length}`, author: "admin", initials: "AD", time: "05:21 PM", text: text.trim(), note: mode === "Note" }]);
          setText("");
        }}><Glyph name="arrow-up" />Send</Button>} />
    </div>
  </PaneToolbarHost>;
}

function Tasks({ page }: { page: "bill" | "knowledge" }) {
  const [done, setDone] = useState<string[]>([]);
  return <PaneToolbarHost>
    <PaneToolbar scope={<ScopeChip label="Planned" count={tasks[page].length - done.length} active onClick={() => undefined} />} />
    <PaneRows className="px-3 py-3"><PaneLabel>Oct 21–23, 2026</PaneLabel>
      {tasks[page].map((task) => <li key={task.title}><PaneRow icon={done.includes(task.title) ? "check" : "circle"} title={task.title}
        sub={task.detail} onOpen={() => setDone(done.includes(task.title) ? done.filter((title) => title !== task.title) : [...done, task.title])} /></li>)}
    </PaneRows>
  </PaneToolbarHost>;
}
