import type { Meta, StoryObj } from "@storybook/react-vite";
import { useState } from "react";
import { PaneToolbarHost, TimelineRail, type TimelineRailEntry, type TimelineRailKind } from "@angee/ui";

/**
 * The Timeline pane: scope chips (All, Needs you, the first kinds; the rest in
 * the pane menu), rows by day with the kind's glyph and verb, folded summaries,
 * and the shared error, loading and empty surfaces.
 */
const meta = {
  title: "Communication/TimelineRail",
  component: TimelineRail,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof TimelineRail>;

export default meta;

type Story = StoryObj;

const noop = () => undefined;
const entries: readonly TimelineRailEntry[] = [
  { key: "run-3", at: "2026-10-20T09:12:00Z", kind: "workflows.run", title: "Invoice review", actor: "Agent", detail: "waiting on you", folded: "2 changes · 1 decision", needsMe: true, href: "#run-3" },
  { key: "comment-9", at: "2026-10-20T08:40:00Z", kind: "messaging.comment", title: "Cloudflare confirmed the PO number", actor: "Sofia Marin", href: "#comment-9" },
  { key: "change-7", at: "2026-10-19T16:05:00Z", kind: "provenance.change", title: "Amount corrected", actor: "admin", detail: "€ 412.00 → € 418.60", href: "#change-7" },
  { key: "task-2", at: "2026-10-18T11:30:00Z", kind: "projects.task", title: "Attach the signed agreement", actor: "Alex Morgan", detail: "due Friday", href: "#task-2" },
  { key: "run-1", at: "2026-10-18T09:00:00Z", kind: "workflows.run", title: "Import", actor: "Agent", folded: "6 changes", href: "#run-1" },
];
const kinds: readonly TimelineRailKind[] = [
  { id: "workflows.run", label: "Runs", icon: "play", tone: "info", verb: "Open run", target: { tab: "run" }, count: 2 },
  { id: "messaging.comment", label: "Comments", icon: "message-square", tone: "muted", verb: "Open", target: { tab: "discussion" }, count: 1 },
  { id: "provenance.change", label: "Changes", icon: "activity", tone: "muted", verb: "Open", target: { tab: "history" }, count: 1 },
  { id: "projects.task", label: "Tasks", icon: "check", tone: "success", verb: "Open", target: { tab: "tasks" }, count: 1 },
];

function Rail(props: Partial<React.ComponentProps<typeof TimelineRail>>) {
  const [scope, setScope] = useState("all");
  return (
    <div className="h-[480px] w-[26rem] border-r border-border-subtle bg-sheet">
      <PaneToolbarHost>
        <TimelineRail entries={entries} kinds={kinds} needsMe={1} scope={scope} onScope={setScope} onOpen={noop} {...props} />
      </PaneToolbarHost>
    </div>
  );
}

/** Rows over three days with a Needs you line, a folded run and four kinds (two past the chips, in the pane menu). */
export const Default: Story = { render: () => <Rail /> };

/** Older entries load from the rail's foot. */
export const LoadsOlder: Story = { render: () => <Rail hasMore onLoadOlder={noop} /> };

/** A read error through the shared banner, with retry. */
export const Error_: Story = { name: "Error", render: () => <Rail entries={[]} error="The timeline could not be read." onRetry={noop} /> };

/** Loading, then empty: the shared loading panel and empty state. */
export const Loading: Story = { render: () => <Rail entries={[]} pending /> };
export const Empty: Story = { render: () => <Rail entries={[]} kinds={[]} needsMe={0} /> };
