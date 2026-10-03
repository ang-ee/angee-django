import { useCallback, useMemo } from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Badge, Button, ErrorBanner, GraphView, InlineEmpty, LoadingPanel, MetaGrid, MetaSection, PageAside, RailPanel,
  errorMessage, optionLabel, routeSearchParam, updateRouteSearch, useChatter, useChatterContent,
  useEnumOptions, useRouteSearch, useStatusTone, type GraphViewNode,
} from "@angee/ui";
import { RUN_MODEL, STEP_RUN_MODEL, WorkflowRunGraphDocument } from "./documents.console";
import { useWorkflowsT } from "./i18n";
import { projectRunGraph, RUN_GRAPH_EDGE_STYLES, type RunGraphNode } from "./run-graph";
import { StepRuns } from "./StepRuns";

export const RUN_GRAPH_INSPECTOR_TAB = "run-graph-inspector";

/** A retained read-only canvas; only the active run tab publishes its inspector and reads. */
export function RunGraph({ runId, active = true }: { runId: string; active?: boolean }) {
  const t = useWorkflowsT();
  const route = useRouteSearch();
  const selectedKey = routeSearchParam(route.search, "node");
  const chatter = useChatter();
  const resolveTone = useStatusTone();
  const statusOptions = useEnumOptions(STEP_RUN_MODEL, "status");
  const waitOptions = useEnumOptions(STEP_RUN_MODEL, "waiting_kind");
  const read = useAuthoredQuery(WorkflowRunGraphDocument, { id: runId }, {
    dataProviderName: "console", models: [RUN_MODEL, STEP_RUN_MODEL],
    records: [{ model: RUN_MODEL, id: runId }], relatedModels: [STEP_RUN_MODEL],
    keepPreviousData: true, enabled: active,
  });
  const run = read.data?.workflowrun_by_pk;
  const graph = run?.id === runId ? run.graph : undefined;
  const projected = useMemo(() => graph ? projectRunGraph(graph, selectedKey, { t, statusOptions, waitOptions, resolveTone }) : null,
    [graph, selectedKey, t, statusOptions, waitOptions, resolveTone]);
  const selected = graph?.nodes.find((node) => node.key === selectedKey);
  const inspector = useMemo(() => active && graph ? { tabs: [{ id: RUN_GRAPH_INSPECTOR_TAB,
    label: t("run.graphInspector"), icon: "info", children:
      <PageAside collapse="never" gutter="compact" className="h-full w-full border-l-0">
        <RailPanel title={t("run.graphInspector")} empty={t("run.graphSelectNode")}>
          {selected ? <RunNodeInspector key={`${runId}:${selected.key}`} runId={runId} node={selected}
            detail={projected?.nodes.find((node) => node.id === selected.key)?.detail} /> : null}
        </RailPanel>
      </PageAside>,
  }] } : null, [active, graph, selected, projected, runId, t]);
  useChatterContent(inspector);
  const selectNode = useCallback((node: GraphViewNode) => {
    route.navigate({ search: updateRouteSearch({ node: node.id }) });
    chatter.setCollapsed(false);
    chatter.setActiveTab(RUN_GRAPH_INSPECTOR_TAB);
  }, [route.navigate, chatter.setCollapsed, chatter.setActiveTab]);
  const errorPanel = read.error ? <ErrorBanner description={errorMessage(read.error, t("run.graphLoadFailed"))}
    actions={<Button onClick={() => void read.refetch()}>{t("run.graphReload")}</Button>} /> : null;
  if (!graph || !projected) return errorPanel ?? (read.data && !run
    ? <InlineEmpty label={t("run.graphUnavailable")} /> : <LoadingPanel message={t("run.graphLoading")} />);
  return <div data-testid="run-graph-canvas" className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
    {errorPanel}
    <GraphView nodes={projected.nodes} edges={projected.edges} nodeStyles={projected.nodeStyles}
      edgeStyles={RUN_GRAPH_EDGE_STYLES} status={projected.status} layout={{ rankdir: "LR" }} miniMap
      initialView={{ ready: true, anchorNodeId: projected.anchorNodeId, minZoom: 0.65 }}
      ariaLabel={t("run.graph")} className="min-h-0 flex-1" onNodeClick={selectNode} />
  </div>;
}

function RunNodeInspector({ runId, node, detail }: {
  runId: string; node: RunGraphNode; detail: GraphViewNode["detail"];
}) {
  const t = useWorkflowsT();
  const resolveTone = useStatusTone();
  const statuses = useEnumOptions(STEP_RUN_MODEL, "status");
  const row = node.step_run;
  return <>
    <MetaSection title={node.label}>
      <MetaGrid rows={[
        [t("run.graphKey"), node.key], [t("run.graphStep"), node.step_label],
        [t("run.status"), <Badge tone={resolveTone(row?.status ?? "pending")}>
          {row ? optionLabel(statuses, row.status) : t("run.graphPending")}</Badge>],
        ...(detail ? [[t("run.graphProgress"), detail] as const] : []),
        ...(row ? [[t("step.attempts"), row.attempt], [t("step.pageIndex"), row.page_index],
          [t("run.started"), row.created_at], [t("step.updated"), row.updated_at],
          ...(row.deadline_at ? [[t("step.deadline"), row.deadline_at] as const] : []),
          ...(row.wake_at ? [[t("step.wake"), row.wake_at] as const] : [])] as const : []),
        ...(node.body_key ? [[t("step.mapSettled"), row?.map_settled ?? 0], [t("step.mapTotal"), row?.map_total ?? 0]] as const : []),
      ]} />
    </MetaSection>
    <StepRuns runId={runId} nodeKeys={node.body_key ? [node.key, node.body_key] : [node.key]}
      selectFirstRecord={!node.body_key && row != null} />
  </>;
}
