import { storyAppearance, type RuntimeFixtureProps } from "@angee/storybook/testing";
import { useState, type ComponentProps } from "react";
import { ChatterTabsTestHost } from "@angee/app/testing";
import { Button, routeSearchParam, useChatter, useRouteSearch } from "@angee/ui";
import { RunGraph } from "./RunGraph";
import { RunStory, type RunRequest } from "./RunsPage.stories";
import { mappedRunGraphFixture, runGraphFixture, stepRunFixture } from "./testing";
import type { RunGraphData } from "./run-graph";

export default { title: "Workflows/Run graph", parameters: { angeeOwnRuntime: true, layout: "fullscreen" }, excludeStories: ["RunGraphStory"] };
export const Failed = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunGraphStory appearance={storyAppearance(globals)} /> };
export const MapProgress = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunGraphStory appearance={storyAppearance(globals)} graph={mappedRunGraphFixture()} /> };
export const Paging = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunGraphStory appearance={storyAppearance(globals)} graph={runGraphFixture(stepRunFixture({
  status: "RUNNING", failure_reason: null, outcome: "", outcome_label: "", page_index: 3,
}))} /> };
export const Retained = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunGraphStory appearance={storyAppearance(globals)} retained /> };

export function RunGraphStory({ appearance, graph, active = true, retained = false, onRequest, initialEntry, liveProvider, queryClient }: {
  appearance?: RuntimeFixtureProps["appearance"];
  graph?: RunGraphData; active?: boolean; retained?: boolean; onRequest?: (request: RunRequest) => void;
  initialEntry?: string; liveProvider?: ComponentProps<typeof RunStory>["liveProvider"];
  queryClient?: ComponentProps<typeof RunStory>["queryClient"];
}) {
  return <RunStory appearance={appearance} graph={graph} onRequest={onRequest} initialEntry={initialEntry} liveProvider={liveProvider} queryClient={queryClient}
    content={<GraphDemo initialActive={active} retained={retained} />} />;
}

function GraphDemo({ initialActive, retained }: { initialActive: boolean; retained: boolean }) {
  const [active, setActive] = useState(initialActive);
  const pane = useChatter();
  const search = useRouteSearch();
  return <>
    {retained ? <div>
      <Button onClick={() => setActive((value) => !value)}>{active ? "Hide graph" : "Show graph"}</Button>
      <Button onClick={() => pane.setCollapsed(true)}>Collapse inspector</Button>
      <output data-testid="graph-pane-state">{String(pane.collapsed)}:{pane.activeTab}</output>
      <output data-testid="graph-node-selection">{routeSearchParam(search, "node")}</output>
    </div> : null}
    <div className="grid min-h-0 flex-1 grid-cols-[1fr_24rem] gap-4">
      <div hidden={!active} className="flex min-h-0 flex-col"><RunGraph runId="wfr_review" active={active} /></div>
      <ChatterTabsTestHost />
    </div>
  </>;
}
