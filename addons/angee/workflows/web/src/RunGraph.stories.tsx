import { useState } from "react";
import { ChatterTabsTestHost } from "@angee/app/testing";
import { Button, routeSearchParam, useChatter, useRouteSearch } from "@angee/ui";
import { RunGraph } from "./RunGraph";
import { RunStory, type RunRequest } from "./RunsPage.stories";
import { mappedRunGraphFixture, runGraphFixture, stepRunFixture } from "./testing";
import type { RunGraphData } from "./run-graph";

export default { title: "Workflows/Run graph", parameters: { layout: "fullscreen" }, excludeStories: ["RunGraphStory"] };
export const Failed = { render: () => <RunGraphStory /> };
export const MapProgress = { render: () => <RunGraphStory graph={mappedRunGraphFixture()} /> };
export const Paging = { render: () => <RunGraphStory graph={runGraphFixture(stepRunFixture({
  status: "RUNNING", failure_reason: null, outcome: "", outcome_label: "", page_index: 3,
}))} /> };
export const Retained = { render: () => <RunGraphStory retained /> };

export function RunGraphStory({ graph, active = true, retained = false, onRequest }: {
  graph?: RunGraphData; active?: boolean; retained?: boolean; onRequest?: (request: RunRequest) => void;
}) {
  return <RunStory graph={graph} onRequest={onRequest}
    content={<GraphDemo initialActive={active} retained={retained} />} />;
}

function GraphDemo({ initialActive, retained }: { initialActive: boolean; retained: boolean }) {
  const [active, setActive] = useState(initialActive);
  const pane = useChatter();
  const route = useRouteSearch();
  return <>
    {retained ? <div>
      <Button onClick={() => setActive((value) => !value)}>{active ? "Hide graph" : "Show graph"}</Button>
      <Button onClick={() => pane.setCollapsed(true)}>Collapse inspector</Button>
      <output data-testid="graph-pane-state">{String(pane.collapsed)}:{pane.activeTab}</output>
      <output data-testid="graph-node-selection">{routeSearchParam(route.search, "node")}</output>
    </div> : null}
    <div className="grid min-h-0 flex-1 grid-cols-[1fr_24rem] gap-4">
      <div hidden={!active} className="flex min-h-0 flex-col"><RunGraph runId="wfr_review" active={active} /></div>
      <ChatterTabsTestHost />
    </div>
  </>;
}
