import type { DocumentType } from "@angee/gql/console";
import {
  graphNodeStyle, optionLabel, optionTextLabel, optionToken,
  type GraphViewEdge, type GraphViewEdgeStyle, type GraphViewNode, type GraphViewStatus, type Tone, type UiTranslate, type WidgetOption,
} from "@angee/ui";
import { toneColorVar } from "@angee/ui/lib/tones";
import { WorkflowRunGraphDocument } from "./documents.console";
import { formatStepPage } from "./step-page";

export type RunGraphData = NonNullable<DocumentType<typeof WorkflowRunGraphDocument>["workflowrun_by_pk"]>["graph"];
export type RunGraphNode = RunGraphData["nodes"][number];

const current = (node: RunGraphNode) => ["ready", "running", "waiting"].includes(optionToken(node.step_run?.status));

export const RUN_GRAPH_EDGE_STYLES = {
  taken: { stroke: "var(--brand)", strokeWidth: 2 },
} satisfies Record<string, GraphViewEdgeStyle>;

/** Project the server's pinned topology and complete aggregates into the shared canvas. */
export function projectRunGraph(graph: RunGraphData, selected: string | undefined, options: {
  t: UiTranslate; statusOptions: readonly WidgetOption[]; waitOptions: readonly WidgetOption[];
  resolveTone: (value: string) => Tone; locale?: string;
}) {
  const { t, statusOptions, waitOptions, resolveTone, locale } = options;
  const list = new Intl.ListFormat(locale, { style: "short", type: "unit" });
  const status: Record<string, GraphViewStatus> = {};
  const nodes: GraphViewNode[] = graph.nodes.map((node) => {
    const row = node.step_run;
    const kind = row ? optionToken(row.status) : "unreached";
    const label = row ? optionLabel(statusOptions, row.status) : t("run.graphPending");
    const badge = node.body_key ? `${row?.map_settled ?? 0}/${row?.map_total ?? 0}`
      : row && row.page_index > 0 ? formatStepPage(row.page_index, t) : label;
    status[node.key] = { label: badge, tone: resolveTone(kind) };
    const detail = node.body_key
      ? list.format([...node.item_counts.map(({ status, count }) => t("run.graphStatusCount", {
        count, status: optionTextLabel(optionLabel(statusOptions, status), status),
      })), t("run.graphAttempts", { count: node.item_attempts })])
      : row?.failure_reason || row?.wait_reason || (row?.waiting_kind
        ? optionTextLabel(optionLabel(waitOptions, row.waiting_kind), row.waiting_kind) : row?.outcome_label);
    return { id: node.key, kind, title: node.label, kindLabel: node.step_label, code: node.key,
      highlighted: current(node), selected: node.key === selected, detail,
      ariaLabel: list.format([node.label, optionTextLabel(label, kind), ...(detail ? [detail] : [])]),
      ports: node.outcomes.map(({ outcome, label }) => ({ id: outcome, label })) };
  });
  const edges: GraphViewEdge<"taken" | "idle">[] = graph.edges.map((edge) => ({
    id: JSON.stringify([edge.source, edge.outcome, edge.target]), source: edge.source, target: edge.target,
    sourceHandle: edge.outcome, kind: edge.taken ? "taken" : "idle",
  }));
  const kinds = new Set(["unreached", ...statusOptions.map((option) => optionToken(option.value)), ...nodes.map((node) => node.kind)]);
  const nodeStyles = Object.fromEntries([...kinds].map((kind) => {
    const tone = resolveTone(kind);
    return [kind, graphNodeStyle(toneColorVar(tone), tone, { width: 244, height: 100,
      highlightedBorderColor: toneColorVar(tone), highlightedBackground: "var(--surface-sheet-2)" })];
  }));
  const ranked = [...graph.nodes].sort((a, b) => a.rank - b.rank || a.key.localeCompare(b.key));
  const anchorNodeId = (ranked.find(current) ?? ranked.find((node) => optionToken(node.step_run?.status) === "failed"))?.key;
  return { nodes, edges, nodeStyles, status, anchorNodeId };
}
