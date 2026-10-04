import type { DocumentType } from "@angee/gql/console";
import {
  graphNodeStyle, optionLabel, optionTextLabel, optionToken,
  type GraphViewEdge, type GraphViewEdgeStyle, type GraphViewNode, type GraphViewStatus, type Tone, type UiTranslate, type WidgetOption,
} from "@angee/ui";
import { toneColorVar } from "@angee/ui/lib/tones";
import type { WorkflowRunGraphDocument } from "./documents.console";
import { formatStepPage } from "./step-page";
import { WORKFLOW_GRAPH_STATUS_TONES } from "./status-tones";

export type RunGraphData = NonNullable<DocumentType<typeof WorkflowRunGraphDocument>["workflowrun_by_pk"]>["graph"];
export type RunGraphNode = RunGraphData["nodes"][number];

const current = (node: RunGraphNode) => ["ready", "running", "waiting"].includes(optionToken(node.step_run?.status));

export const RUN_GRAPH_EDGE_STYLES = {
  taken: { stroke: "var(--brand)", strokeWidth: 2 },
} satisfies Record<string, GraphViewEdgeStyle>;

/** Project the server's pinned topology and complete aggregates into the shared canvas. */
export function projectRunGraph(graph: RunGraphData, selected: string | undefined, options: {
  t: UiTranslate; statusOptions: readonly WidgetOption[]; waitOptions: readonly WidgetOption[];
  resolveTone: (value: string, override?: Record<string, Tone>) => Tone; locale?: string;
}) {
  const { t, statusOptions, waitOptions, resolveTone, locale } = options;
  const list = new Intl.ListFormat(locale, { style: "short", type: "unit" });
  const status: Record<string, GraphViewStatus> = {};
  const nodes: GraphViewNode[] = graph.nodes.map((node) => {
    const row = node.step_run;
    const kind = row ? optionToken(row.status) : "unreached";
    const label = row ? optionLabel(statusOptions, row.status) : t("run.graphPending");
    status[node.key] = { label, tone: resolveTone(kind, WORKFLOW_GRAPH_STATUS_TONES) };
    const summary = node.body_key
      ? (row && row.map_total > 0 ? list.format([`${row.map_settled}/${row.map_total}`,
        ...node.item_counts.map(({ status, count }) => t("run.graphStatusCount", {
          count, status: optionTextLabel(optionLabel(statusOptions, status), status),
        })), t("run.graphAttempts", { count: node.item_attempts })]) : undefined)
      : row?.failure_reason || row?.wait_reason || (row?.waiting_kind
        ? optionTextLabel(optionLabel(waitOptions, row.waiting_kind), row.waiting_kind) : row?.outcome_label);
    const page = row && ["running", "waiting"].includes(kind) && row.page_index > 0
      ? formatStepPage(row.page_index, t) : undefined;
    const detail = list.format([...(page ? [page] : []), ...(summary ? [summary] : [])]);
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
    const tone = resolveTone(kind, WORKFLOW_GRAPH_STATUS_TONES);
    const border = ["unreached", "skipped", "canceled"].includes(kind)
      ? `color-mix(in srgb, ${toneColorVar(tone)} 40%, var(--surface-sheet))` : toneColorVar(tone);
    return [kind, graphNodeStyle(border, tone, { width: 244, height: 100,
      highlightedBorderColor: toneColorVar(tone), highlightedBackground: "var(--surface-sheet-2)" })];
  }));
  const ranked = [...graph.nodes].sort((a, b) => a.rank - b.rank || a.key.localeCompare(b.key));
  const anchorNodeId = (ranked.find(current)
    ?? ranked.find((node) => ["failed", "canceled"].includes(optionToken(node.step_run?.status))))?.key;
  return { nodes, edges, nodeStyles, status, anchorNodeId };
}
