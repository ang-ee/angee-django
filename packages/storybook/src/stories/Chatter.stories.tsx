import type { Meta, StoryObj } from "@storybook/react-vite";
import { useMemo, useState } from "react";
import {
  AppRuntimeProvider, CHATTER_CONTAINERS, Chatter, ChatterProvider, PaneLabel, PaneLabelAction, PaneRow, PaneRows,
  PaneToolbar, PaneToolbarHost, RelatedPane, StepList, TimelineRail, containersFromChildren, useAppRuntime, useChatter,
  type ChatterTab, type ChatterViewContext, type RelatedGroupContent, type TimelineRailEntry, type TimelineRailKind,
} from "@angee/ui";

/**
 * The context aside: strip tabs with counts, each pane under its own toolbar
 * host, and drill-ins reached from a line with the way back above them.
 */
const meta = {
  title: "Communication/Chatter",
  component: Chatter,
  parameters: { layout: "fullscreen", owner: [Chatter, ChatterProvider, PaneToolbarHost] },
} satisfies Meta<typeof Chatter>;

export default meta;

type Story = StoryObj;

const noop = () => undefined;
const entries: readonly TimelineRailEntry[] = [
  { key: "run-3", at: "2026-10-20T09:12:00Z", kind: "workflows.run", title: "Invoice review", actor: "Agent", detail: "waiting on you", folded: "2 changes · 1 decision", needsMe: true },
  { key: "comment-9", at: "2026-10-20T08:40:00Z", kind: "messaging.comment", title: "Cloudflare confirmed the PO number", actor: "Sofia Marin" },
  { key: "change-7", at: "2026-10-19T16:05:00Z", kind: "provenance.change", title: "Amount corrected", actor: "admin", detail: "€ 412.00 → € 418.60" },
  { key: "task-2", at: "2026-10-18T11:30:00Z", kind: "projects.task", title: "Attach the signed agreement", actor: "Alex Morgan", detail: "due Friday" },
  { key: "run-1", at: "2026-10-18T09:00:00Z", kind: "workflows.run", title: "Import", actor: "Agent", folded: "6 changes" },
];
const kinds: readonly TimelineRailKind[] = [
  { id: "workflows.run", label: "Runs", icon: "play", tone: "info", verb: "Open run", target: { tab: "run" }, count: 2 },
  { id: "messaging.comment", label: "Comments", icon: "message-square", tone: "muted", verb: "Open", target: { tab: "discussion" }, count: 1 },
  { id: "provenance.change", label: "Changes", icon: "activity", tone: "muted", verb: "Open", target: { tab: "history" }, count: 1 },
  { id: "projects.task", label: "Tasks", icon: "check", tone: "success", verb: "Open", target: { tab: "tasks" }, count: 1 },
];
const context: ChatterViewContext = {
  pathname: "/notes/bill_9311", params: {},
  view: { kind: "record", type: "accounting.Invoice", sqid: "bill_9311" },
};
const files: RelatedGroupContent = {
  label: "Files",
  useCount: () => 2,
  useVerb: () => true,
  render: (_context, label) => (
    <>
      <li>{label(<PaneLabelAction icon="upload">Upload</PaneLabelAction>)}</li>
      <li><PaneRow icon="file" title="v6-cloudflare-9311.xml" meta="5.8 KB" sub="uploaded by admin" href="#file" label="Open file" /></li>
      <li><PaneRow icon="file" title="cloudflare-agreement.pdf" meta="412 KB" sub="uploaded by Sofia Marin" href="#file" label="Open file" /></li>
    </>
  ),
};
const relatedContainers = containersFromChildren(CHATTER_CONTAINERS, {
  "related#groups": { "story.files": { content: files } },
});

function Timeline() {
  const [scope, setScope] = useState("all");
  const { setActiveTab } = useChatter();
  return (
    <TimelineRail entries={entries} kinds={kinds} scope={scope} onScope={setScope} needsMe={1}
      onOpen={(entry) => {
        const kind = kinds.find((candidate) => candidate.id === entry.kind);
        if (kind && "tab" in kind.target) setActiveTab(kind.target.tab, { from: "timeline", target: entry.key });
      }} />
  );
}

function Tasks() {
  return (
    <PaneToolbarHost>
      <PaneToolbar actions={[{ icon: "plus", label: "New task", onClick: noop }]} />
      <PaneRows className="px-2 py-1">
        <li><PaneRow icon="circle" title="Attach the signed agreement" detail="due Friday" sub="Alex Morgan" onOpen={noop}
          verb={<PaneLabelAction icon="check">Done</PaneLabelAction>} /></li>
        <li><PaneRow icon="check" tone="success" title="Match the purchase order" sub="Sofia Marin" onOpen={noop} /></li>
      </PaneRows>
    </PaneToolbarHost>
  );
}

function Discussion() {
  return (
    <PaneToolbarHost>
      <PaneToolbar search onSearch={noop} actions={[{ icon: "check", label: "Mark read", onClick: noop }]} />
      <PaneRows className="px-2 py-1">
        <PaneLabel>Today</PaneLabel>
        <li><PaneRow icon="message-square" title="Cloudflare confirmed the PO number" sub="Sofia Marin" meta="08:40" onOpen={noop} /></li>
        <li><PaneRow icon="message-square" title="Reconciled against the bank line" sub="admin" meta="07:55" onOpen={noop} /></li>
      </PaneRows>
    </PaneToolbarHost>
  );
}

function Run() {
  return (
    <StepList items={[
      { id: "import", state: "done", title: "Import the document", outcome: "6 changes" },
      { id: "review", state: "current", title: "Review the lines", detail: "Waiting on you" },
      { id: "post", state: "planned", title: "Post the bill" },
    ]} />
  );
}

const tabs: readonly ChatterTab[] = [
  { id: "timeline", label: "Timeline", icon: "activity", count: 5, children: <Timeline /> },
  { id: "discussion", label: "Discussion", icon: "message-square", count: 2, children: <Discussion /> },
  { id: "tasks", label: "Tasks", icon: "check", count: 1, children: <Tasks /> },
  { id: "related", label: "Related", icon: "link", count: 2, children: <RelatedPane context={context} /> },
  { id: "run", label: "Run", icon: "play", strip: false, children: <Run /> },
  { id: "history", label: "Change", icon: "activity", strip: false, children: <Run /> },
];

function Aside({ defaultTab = "timeline" }: { defaultTab?: string }) {
  const runtime = useAppRuntime();
  const fixtureRuntime = useMemo(() => ({ ...runtime, containers: relatedContainers }), [runtime]);
  return (
    <AppRuntimeProvider runtime={fixtureRuntime}>
      <ChatterProvider defaultTab={defaultTab}>
        <div className="h-[560px] w-[26rem] border-l border-border-subtle bg-sheet">
          <Chatter tabs={tabs} />
        </div>
      </ChatterProvider>
    </AppRuntimeProvider>
  );
}

/** Four strip tabs with counts; the Timeline's scope chips and the pane menu's radio group for the rest; a row opens its drill-in. */
export const Default: Story = { render: () => <Aside /> };

/** The Related tab: each eligible group with its count and verb, then its rows. */
export const Related: Story = { render: () => <Aside defaultTab="related" /> };

/** The Tasks pane: a verb beside each row as the pane's own control. */
export const Tasks_: Story = { name: "Tasks", render: () => <Aside defaultTab="tasks" /> };
