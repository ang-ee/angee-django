import type { Meta, StoryObj } from "@storybook/react-vite";
import { useMemo } from "react";
import {
  AppRuntimeProvider, CHATTER_CONTAINERS, ChatterProvider, PaneLabelAction, PaneRow, PaneToolbarHost, RelatedPane,
  containersFromChildren, useAppRuntime, type ChatterViewContext, type RelatedGroupContent,
} from "@angee/ui";

/**
 * The Related tab: one group per edge the record carries (files, pages,
 * sources), each with its count and at most one verb, then its rows. Groups
 * are `related#groups` children declared by the edge's owner.
 */
const meta = {
  title: "Communication/RelatedPane",
  component: RelatedPane,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof RelatedPane>;

export default meta;

type Story = StoryObj;

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
const pages: RelatedGroupContent = {
  label: "Pages",
  useCount: () => 1,
  useVerb: () => true,
  render: (_context, label) => (
    <>
      <li>{label(<PaneLabelAction icon="link">Bind</PaneLabelAction>)}</li>
      <li><PaneRow icon="book-open" title="Cloudflare, Inc. agreement" sub="Accounts Payable / Supplier contracts" href="#page" label="Open page" /></li>
    </>
  ),
};
const sources: RelatedGroupContent = {
  label: "Sources",
  useCount: () => 0,
  useVerb: () => false,
  render: () => null,
};

function Pane({ groups }: { groups: Record<string, RelatedGroupContent> }) {
  const runtime = useAppRuntime();
  const fixtureRuntime = useMemo(() => ({
    ...runtime,
    containers: containersFromChildren(CHATTER_CONTAINERS, {
      "related#groups": Object.fromEntries(Object.entries(groups).map(([id, content]) => [id, { content }])),
    }),
  }), [runtime, groups]);
  return (
    <AppRuntimeProvider runtime={fixtureRuntime}>
      <ChatterProvider>
        <div className="h-[420px] w-[26rem] border-r border-border-subtle bg-sheet">
          <PaneToolbarHost title="Related"><RelatedPane context={context} /></PaneToolbarHost>
        </div>
      </ChatterProvider>
    </AppRuntimeProvider>
  );
}

/** Files with an Upload verb, Pages with Bind; a group with neither rows nor a verb is absent. */
export const Default: Story = { render: () => <Pane groups={{ "story.files": files, "story.pages": pages, "story.sources": sources }} /> };

/** No eligible group: the shared empty state. */
export const Empty: Story = { render: () => <Pane groups={{}} /> };
