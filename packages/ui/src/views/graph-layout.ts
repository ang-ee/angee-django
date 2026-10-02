import * as dagre from "@dagrejs/dagre";
import type { Rect } from "@xyflow/react";
import type { GraphViewLayout, GraphViewPosition } from "./GraphView";

export const DEFAULT_GRAPH_LAYOUT: Required<GraphViewLayout> = {
  rankdir: "TB",
  nodesep: 34,
  ranksep: 76,
  edgesep: 18,
  marginx: 24,
  marginy: 24,
};

export function layoutGraph<
  TEdgeKind extends string,
>({
  nodes,
  edges,
  layout,
}: {
  nodes: readonly { id: string; position?: GraphViewPosition; width: number; height: number }[];
  edges: readonly { id: string; source: string; target: string; kind: TEdgeKind }[];
  layout?: GraphViewLayout;
}): {
  positions: ReadonlyMap<string, GraphViewPosition>;
  visibleEdgeIds: ReadonlySet<string>;
} {
  // @dagrejs/dagre is pinned EXACT at 3.0.0: 3.1.0/3.1.1 regress on large
  // multigraphs with >=3 parallel edges between one node pair (dagre's
  // intersectRect throws "Not possible to find intersection inside of the
  // rectangle" — live repro: the platform model graph's created_by/updated_by/
  // owner edges). Re-test with the platform graph before widening the range.
  const graph = new dagre.graphlib.Graph({ directed: true, multigraph: true });
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ ...DEFAULT_GRAPH_LAYOUT, ...layout });

  const nodeIds = new Set(nodes.map((node) => node.id));
  for (const node of nodes) {
    graph.setNode(node.id, { width: node.width, height: node.height });
  }
  for (const edge of edges) {
    // Dagre throws "Not possible to find intersection inside of the
    // rectangle" for edges it cannot lay out: an endpoint that is not a
    // declared node (setEdge would auto-create it dimensionless) or a
    // self-loop. Live graph data contains both (filtered-out endpoints,
    // self-referential relations), so the layout skips them; self-loops
    // still render — React Flow draws them without dagre's help.
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue;
    if (edge.source === edge.target) continue;
    graph.setEdge(edge.source, edge.target, { weight: 1 }, edge.id);
  }

  dagre.layout(graph);

  return {
    positions: new Map(nodes.map((node) => {
      const position = graph.node(node.id);
      return [node.id, node.position ?? {
        x: position.x - node.width / 2,
        y: position.y - node.height / 2,
      }];
    })),
    visibleEdgeIds: new Set(edges
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map((edge) => edge.id)),
  };
}


/** Search alternating lanes, then use the nearest exterior lane when all are occupied. */
export function findFreeGraphPosition(
  size: { width: number; height: number },
  preferred: GraphViewPosition,
  occupied: readonly Rect[],
  axis: "horizontal" | "vertical" = "horizontal",
  gap = DEFAULT_GRAPH_LAYOUT.nodesep,
): GraphViewPosition {
  const step = Math.max(1, (axis === "horizontal" ? size.width : size.height) + gap);
  const collides = (point: GraphViewPosition) => occupied.some((rect) => point.x < rect.x + rect.width && point.x + size.width > rect.x && point.y < rect.y + rect.height && point.y + size.height > rect.y);
  for (let distance = 0; distance <= occupied.length; distance += 1) {
    for (const direction of distance === 0 ? [0] : [1, -1]) {
      const offset = distance * direction * step;
      const candidate = axis === "horizontal" ? { x: preferred.x + offset, y: preferred.y } : { x: preferred.x, y: preferred.y + offset };
      if (!collides(candidate)) return candidate;
    }
  }
  const low = Math.min(...occupied.map((rect) => axis === "horizontal" ? rect.x : rect.y));
  const high = Math.max(...occupied.map((rect) => axis === "horizontal" ? rect.x + rect.width : rect.y + rect.height));
  const before = low - (axis === "horizontal" ? size.width : size.height) - Math.max(0, gap);
  const after = high + Math.max(0, gap);
  const origin = axis === "horizontal" ? preferred.x : preferred.y;
  const value = Math.abs(before - origin) < Math.abs(after - origin) ? before : after;
  return axis === "horizontal" ? { x: value, y: preferred.y } : { x: preferred.x, y: value };
}

/** Place a new node beside an anchor without moving existing nodes. */
export function placeGraphNodeBeside(
  anchor: Rect,
  size: { width: number; height: number },
  occupied: readonly Rect[] = [],
  direction: "right" | "below" = "below",
  layout: GraphViewLayout = {},
): GraphViewPosition {
  const settings = { ...DEFAULT_GRAPH_LAYOUT, ...layout };
  const preferred = direction === "right"
    ? { x: anchor.x + anchor.width + settings.nodesep, y: anchor.y }
    : { x: anchor.x, y: anchor.y + anchor.height + settings.ranksep };
  return findFreeGraphPosition(size, preferred, occupied, direction === "right" ? "vertical" : "horizontal", settings.nodesep);
}
