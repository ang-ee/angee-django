import type { Meta, StoryObj } from "@storybook/react-vite";
import { composeAddons, defineAddon } from "@angee/app";
import * as React from "react";
import {
  Activity,
  Button,
  type ActivityEntry,
  type ActivityEntrySource,
  type ChatterViewContext,
} from "@angee/ui";

import { RuntimeRegistryFixture } from "./runtime-fixtures";

const now = Date.now();
const minute = 60_000;
const entries: readonly ActivityEntry[] = [
  { at: new Date(now - minute), kind: "comment", actor: "Alex", title: "Commented", body: "Please verify the payment terms.", reference: null },
  { at: new Date(now - 2 * minute), kind: "change", actor: "Robin", title: "Changed due date", body: "Oct 22 → Oct 29", reference: "Revision 18" },
  { at: new Date(now - 3 * minute), kind: "run", actor: "Automation", title: "Approval flow finished", body: "3 steps completed", reference: "Run 104" },
  { at: new Date(now - 4 * minute), kind: "decision", actor: "Sam", title: "Payment approved", body: "Approved without conditions", reference: null },
  { at: new Date(now - 5 * minute), kind: "conversation", actor: "Accounts", title: "Invoice received by email", body: "2 messages", reference: "Open conversation" },
  { at: new Date(now - 6 * minute), kind: "source-event", actor: "ERP", title: "Source refreshed", body: "Remote invoice version 7", reference: "Open source" },
];

const source: ActivityEntrySource = {
  kinds: ["comment", "change", "run", "decision", "conversation", "source-event"],
  useEntries: (_record, window) => entries.slice(0, window.limit),
};

const containers = composeAddons([defineAddon({
  id: "activity-story",
  containers: {
    "record#activity": {
      "activity-story.entries": { content: source },
    },
  },
})], { canonicalModelLabel: (model) => model }).containers;

const context: ChatterViewContext = {
  pathname: "/bills/bill_1",
  params: { id: "bill_1" },
  route: { name: "bills.record", path: "/bills/$id", viewType: "record", modelLabel: "billing.Bill" },
  view: { kind: "record", type: "record", sqid: "bill_1" },
};

const meta = {
  title: "Communication/Activity",
  component: Activity,
  parameters: { layout: "padded" },
} satisfies Meta<typeof Activity>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Every source kind shares one chronology, filter row, empty state and paging owner. */
export const AllKinds: Story = {
  args: { context },
  render: (args) => <RuntimeRegistryFixture runtime={{ containers }}>
    <div className="h-[44rem] max-w-xl overflow-hidden rounded-8 border border-border-subtle bg-sheet">
      <Activity {...args} />
    </div>
  </RuntimeRegistryFixture>,
};

/** Composer placement is owned by Activity; a source only publishes its existing composer. */
export const WithComposer: Story = {
  args: { context },
  render: (args) => {
    const composerSource: ActivityEntrySource = {
      ...source,
      useEntries: useComposerEntries,
    };
    const composerContainers = composeAddons([defineAddon({ id: "activity-story", containers: {
      "record#activity": { "activity-story.entries": { content: composerSource } },
    } })], { canonicalModelLabel: (model) => model }).containers;
    return <RuntimeRegistryFixture runtime={{ containers: composerContainers }}>
      <div className="h-[44rem] max-w-xl overflow-hidden rounded-8 border border-border-subtle bg-sheet"><Activity {...args} /></div>
    </RuntimeRegistryFixture>;
  },
};

function useComposerEntries(record: Parameters<ActivityEntrySource["useEntries"]>[0], window: Parameters<ActivityEntrySource["useEntries"]>[1]) {
  const composer = React.useMemo(() => <div className="flex gap-2"><input aria-label="Comment" className="min-w-0 flex-1 rounded-6 border px-2" /><Button size="sm">Send</Button></div>, []);
  React.useEffect(() => {
    window.publishComposer(composer);
    return () => window.publishComposer(null);
  }, [composer, window.publishComposer]);
  return source.useEntries(record, window);
}
