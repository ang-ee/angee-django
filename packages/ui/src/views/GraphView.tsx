/// <reference path="./css.d.ts" />
import * as React from "react";
import * as dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  applyEdgeChanges,
  applyNodeChanges,
  type Edge,
  type FitViewOptions,
  type Node,
  type ReactFlowInstance,
  type Rect,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { cn } from "../lib/cn";
import { useValueStable } from "../lib/use-value-stable";
import { type Tone } from "../lib/tones";
import { Badge } from "../ui/badge";
import { Code } from "../ui/code";
import { textRoleVariants } from "../ui/text";

export interface GraphViewNode<
  TKind extends string = string,
  TMeta extends Record<string, unknown> = Record<string, unknown>,
> {
  id: string;
  kind: TKind;
  kindLabel?: React.ReactNode;
  ariaLabel?: string;
  title: React.ReactNode;
  code?: React.ReactNode;
  detail?: React.ReactNode;
  /** Controlled native React Flow selection state. */
  selected?: boolean;
  highlighted?: boolean;
  position?: GraphViewPosition;
  meta?: TMeta;
}

export interface GraphViewEdge<
  TKind extends string = string,
  TMeta extends Record<string, unknown> = Record<string, unknown>,
> {
  id: string;
  source: string;
  target: string;
  kind: TKind;
  ariaLabel?: string;
  label?: React.ReactNode;
  /** Controlled native React Flow selection state. */
  selected?: boolean;
  meta?: TMeta;
}

export interface GraphViewNodeStyle {
  width: number;
  height: number;
  borderColor: string;
  highlightedBorderColor?: string;
  background?: string;
  highlightedBackground?: string;
  color?: string;
  badgeTone?: Tone;
  type?: "default" | "input" | "output";
}

/** Project theme colors and node chrome into the GraphView style contract. */
export function graphNodeStyle(
  borderColor: string,
  badgeTone: GraphViewNodeStyle["badgeTone"],
  options: Partial<Omit<GraphViewNodeStyle, "borderColor" | "badgeTone">> = {},
): GraphViewNodeStyle {
  const {
    width = 188,
    height = 76,
    highlightedBorderColor = "var(--brand)",
    ...style
  } = options;
  return { width, height, borderColor, highlightedBorderColor, badgeTone, ...style };
}

export interface GraphViewEdgeStyle {
  stroke?: string;
  /** Rendered line width; graph consumers can encode edge strength without custom edges. */
  strokeWidth?: number;
  labelColor?: string;
}

export interface GraphViewLayout {
  rankdir?: "TB" | "BT" | "LR" | "RL";
  nodesep?: number;
  ranksep?: number;
  edgesep?: number;
  marginx?: number;
  marginy?: number;
}

export interface GraphViewPosition {
  x: number;
  y: number;
}

export interface GraphViewConnection {
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
}

export interface GraphViewGeometry<TNodeKind extends string = string> {
  nodeBounds: (id: string) => Rect | undefined;
  nodeSize: (kind: TNodeKind) => { width: number; height: number };
  layout: () => Required<GraphViewLayout>;
  intersects: (bounds: Rect, excludeIds?: ReadonlySet<string>) => boolean;
  firstFreePosition: (kind: TNodeKind, preferred: GraphViewPosition, axis?: "horizontal" | "vertical", excludeIds?: ReadonlySet<string>) => GraphViewPosition;
}

export interface GraphViewProps<
  TNodeKind extends string = string,
  TEdgeKind extends string = string,
  TNodeMeta extends Record<string, unknown> = Record<string, unknown>,
  TEdgeMeta extends Record<string, unknown> = Record<string, unknown>,
> {
  nodes: readonly GraphViewNode<TNodeKind, TNodeMeta>[];
  edges: readonly GraphViewEdge<TEdgeKind, TEdgeMeta>[];
  nodeStyles: Readonly<Record<TNodeKind, GraphViewNodeStyle>>;
  edgeStyles?: Readonly<Partial<Record<TEdgeKind, GraphViewEdgeStyle>>>;
  defaultEdgeStyle?: GraphViewEdgeStyle;
  layout?: GraphViewLayout;
  fitViewOptions?: FitViewOptions;
  /** Imperative read-only access to React Flow's rendered node geometry. */
  geometryRef?: React.Ref<GraphViewGeometry<TNodeKind>>;
  /** Programmatic focus target for hosts that swap the graph with another pane. */
  surfaceRef?: React.Ref<HTMLDivElement>;
  /** Accessible name for the focusable graph surface. */
  ariaLabel?: string;
  className?: string;
  onNodeClick?: (node: GraphViewNode<TNodeKind, TNodeMeta>, activation: GraphViewActivation) => void;
  onEdgeClick?: (edge: GraphViewEdge<TEdgeKind, TEdgeMeta>, activation: GraphViewActivation) => void;
  nodesDraggable?: boolean;
  onNodeDragEnd?: (
    node: GraphViewNode<TNodeKind, TNodeMeta>,
    position: GraphViewPosition,
  ) => void;
  onConnect?: (edge: GraphViewConnection) => void;
  onNodeSelect?: (node: GraphViewNode<TNodeKind, TNodeMeta> | null) => void;
  /** Full current selection for graph actions that require more than one node. */
  onNodesSelect?: (nodes: readonly GraphViewNode<TNodeKind, TNodeMeta>[]) => void;
  onEdgeSelect?: (edge: GraphViewEdge<TEdgeKind, TEdgeMeta> | null) => void;
}

export interface GraphViewActivation {
  /** Keyboard includes native zero-detail assistive activation; it does not assert hardware provenance. */
  source: "keyboard" | "pointer";
}

interface GraphViewNodeData<
  TKind extends string,
  TMeta extends Record<string, unknown>,
> extends Record<string, unknown> {
  node: GraphViewNode<TKind, TMeta>;
  label: React.ReactNode;
}

interface GraphViewEdgeData<
  TKind extends string,
  TMeta extends Record<string, unknown>,
> extends Record<string, unknown> {
  edge: GraphViewEdge<TKind, TMeta>;
}

type RenderNode<
  TKind extends string,
  TMeta extends Record<string, unknown>,
> = Node<GraphViewNodeData<TKind, TMeta>>;

type RenderEdge<
  TKind extends string,
  TMeta extends Record<string, unknown>,
> = Edge<GraphViewEdgeData<TKind, TMeta>>;

const DEFAULT_EDGE_STYLE: Required<GraphViewEdgeStyle> = {
  stroke: "var(--border-strong)",
  strokeWidth: 1,
  labelColor: "var(--text-muted)",
};
const EMPTY_EDGE_STYLES = {} as Readonly<Partial<Record<string, GraphViewEdgeStyle>>>;
const DEFAULT_FIT_VIEW_OPTIONS: FitViewOptions = { padding: 0.18 };

const DEFAULT_LAYOUT: Required<GraphViewLayout> = {
  rankdir: "TB",
  nodesep: 34,
  ranksep: 76,
  edgesep: 18,
  marginx: 24,
  marginy: 24,
};

export function GraphView<
  TNodeKind extends string = string,
  TEdgeKind extends string = string,
  TNodeMeta extends Record<string, unknown> = Record<string, unknown>,
  TEdgeMeta extends Record<string, unknown> = Record<string, unknown>,
>({
  nodes,
  edges,
  nodeStyles,
  edgeStyles = EMPTY_EDGE_STYLES as Readonly<Partial<Record<TEdgeKind, GraphViewEdgeStyle>>>,
  defaultEdgeStyle,
  layout,
  fitViewOptions = DEFAULT_FIT_VIEW_OPTIONS,
  geometryRef,
  surfaceRef,
  ariaLabel,
  className,
  onNodeClick,
  onEdgeClick,
  nodesDraggable = false,
  onNodeDragEnd,
  onConnect,
  onNodeSelect,
  onNodesSelect,
  onEdgeSelect,
}: GraphViewProps<
  TNodeKind,
  TEdgeKind,
  TNodeMeta,
  TEdgeMeta
>): React.ReactElement {
  // Consumers pass `layout` as an inline literal; resolve it by value so a
  // parent re-render with unchanged settings cannot re-run the dagre layout.
  const {
    rankdir = DEFAULT_LAYOUT.rankdir,
    nodesep = DEFAULT_LAYOUT.nodesep,
    ranksep = DEFAULT_LAYOUT.ranksep,
    edgesep = DEFAULT_LAYOUT.edgesep,
    marginx = DEFAULT_LAYOUT.marginx,
    marginy = DEFAULT_LAYOUT.marginy,
  } = layout ?? DEFAULT_LAYOUT;
  const resolvedLayout = React.useMemo(
    () => ({ rankdir, nodesep, ranksep, edgesep, marginx, marginy }),
    [rankdir, nodesep, ranksep, edgesep, marginx, marginy],
  );
  // Style declarations are small semantic records. Consumers commonly build
  // them inline; preserve identity while their values are unchanged so a
  // cosmetic parent render cannot invoke Dagre again.
  const resolvedNodeStyles = useValueStable(nodeStyles);
  const resolvedEdgeStyles = useValueStable(edgeStyles);
  const resolvedDefaultEdgeStyle = useValueStable(defaultEdgeStyle);
  const geometryNodes = useValueStable(nodes.map((node) => {
    const style = nodeStyleFor(node.kind, nodeStyles);
    return {
      id: node.id,
      position: node.position,
      width: style.width,
      height: style.height,
    };
  }));
  const geometryEdges = useValueStable(edges.map((edge) => ({
    id: edge.id,
    kind: edge.kind,
    source: edge.source,
    target: edge.target,
  })));
  const geometryLayout = React.useMemo(
    () =>
      layoutGraph({
        nodes: geometryNodes,
        edges: geometryEdges,
        layout: resolvedLayout,
      }),
    [geometryEdges, geometryNodes, resolvedLayout],
  );
  const layoutedGraph = React.useMemo(() => {
    return {
      nodes: nodes.map((node) => ({
        ...toReactFlowNode(node, resolvedNodeStyles),
        position: geometryLayout.positions.get(node.id) ?? node.position ?? { x: 0, y: 0 },
      })),
      edges: edges
        .filter((edge) => geometryLayout.visibleEdgeIds.has(edge.id))
        .map((edge) => toReactFlowEdge(edge, resolvedEdgeStyles, resolvedDefaultEdgeStyle)),
    };
  }, [edges, geometryLayout, nodes, resolvedDefaultEdgeStyle, resolvedEdgeStyles, resolvedNodeStyles]);
  const [renderNodes, setRenderNodes] = React.useState(layoutedGraph.nodes);
  const [renderEdges, setRenderEdges] = React.useState(layoutedGraph.edges);
  const instanceRef = React.useRef<ReactFlowInstance<RenderNode<TNodeKind, TNodeMeta>, RenderEdge<TEdgeKind, TEdgeMeta>> | null>(null);
  React.useEffect(() => {
    setRenderNodes(layoutedGraph.nodes);
    setRenderEdges(layoutedGraph.edges);
  }, [layoutedGraph]);
  // React Flow re-emits selection state whenever its store adopts replaced
  // nodes. Consumers set state from these callbacks, so re-emitting an
  // unchanged selection loops: setState → re-render → store resync → re-emit
  // ("Maximum update depth exceeded" in StoreUpdater). Emit only on change.
  const lastSelectionSignature = React.useRef<string | null>(null);
  React.useImperativeHandle(geometryRef, () => ({
    nodeBounds: (id) => {
      const instance = instanceRef.current;
      const node = instance?.getNode(id);
      if (!instance || !node) return undefined;
      const bounds = instance.getNodesBounds([id]);
      const declared = nodeStyleFor(node.data.node.kind, nodeStyles);
      return {
        ...bounds,
        width: bounds.width > 0 ? bounds.width : declared.width,
        height: bounds.height > 0 ? bounds.height : declared.height,
      };
    },
    nodeSize: (kind) => {
      const style = nodeStyleFor(kind, nodeStyles);
      return { width: style.width, height: style.height };
    },
    layout: () => resolvedLayout,
    intersects: (bounds, excludeIds = new Set()) => (
      instanceRef.current?.getIntersectingNodes(bounds, true).some((node) => !excludeIds.has(node.id)) ?? false
    ),
    firstFreePosition: (kind, preferred, axis = "horizontal", excludeIds = new Set()) => {
      const instance = instanceRef.current;
      const style = nodeStyleFor(kind, nodeStyles);
      if (!instance) return preferred;
      const step = axis === "horizontal" ? style.width + resolvedLayout.nodesep : style.height + resolvedLayout.ranksep;
      const occupied = (position: GraphViewPosition) => instance.getIntersectingNodes({ ...position, width: style.width, height: style.height }, true).some((node) => !excludeIds.has(node.id));
      const bounds = instance.getNodesBounds(instance.getNodes());
      const span = axis === "horizontal" ? bounds.width + style.width : bounds.height + style.height;
      const limit = Math.ceil(span / step) + 1;
      for (let distance = 0; distance <= limit; distance += 1) {
        for (const direction of distance === 0 ? [0] : [1, -1]) {
          const offset = distance * direction * step;
          const candidate = axis === "horizontal" ? { x: preferred.x + offset, y: preferred.y } : { x: preferred.x, y: preferred.y + offset };
          if (!occupied(candidate)) return candidate;
        }
      }
      const exterior = axis === "horizontal"
        ? [
            { x: bounds.x + bounds.width + resolvedLayout.nodesep, y: preferred.y },
            { x: bounds.x - style.width - resolvedLayout.nodesep, y: preferred.y },
          ]
        : [
            { x: preferred.x, y: bounds.y + bounds.height + resolvedLayout.ranksep },
            { x: preferred.x, y: bounds.y - style.height - resolvedLayout.ranksep },
          ];
      return exterior.sort((left, right) => Math.hypot(left.x - preferred.x, left.y - preferred.y) - Math.hypot(right.x - preferred.x, right.y - preferred.y))[0]!;
    },
  }), [geometryRef, nodeStyles, resolvedLayout]);

  return (
    <div
      ref={surfaceRef}
      tabIndex={-1}
      role={ariaLabel ? "region" : undefined}
      aria-label={ariaLabel}
      className={cn("min-h-0 outline-none", className)}
    >
      <ReactFlow
        onInit={(instance) => { instanceRef.current = instance; }}
        nodes={renderNodes}
        edges={renderEdges}
        onKeyDown={(event) => {
          if (!isGraphActivationKey(event.key)) return;
          if (!(event.target instanceof Element)) return;
          const nodeId = event.target.getAttribute("data-graph-node-id");
          const edgeId = event.target.getAttribute("data-graph-edge-id");
          if (nodeId) {
            const node = instanceRef.current?.getNode(nodeId);
            if (node) {
              event.preventDefault();
              onNodeClick?.(node.data.node, { source: "keyboard" });
            }
          } else if (edgeId) {
            const edge = instanceRef.current?.getEdge(edgeId);
            if (edge?.data?.edge) {
              event.preventDefault();
              onEdgeClick?.(edge.data.edge, { source: "keyboard" });
            }
          }
        }}
        onNodesChange={(changes) => {
          setRenderNodes((current) => applyNodeChanges(changes, current));
        }}
        onEdgesChange={(changes) => {
          setRenderEdges((current) => applyEdgeChanges(changes, current));
        }}
        fitView
        fitViewOptions={fitViewOptions}
        nodesDraggable={nodesDraggable}
        nodesConnectable={Boolean(onConnect)}
        elementsSelectable={Boolean(onNodeSelect || onNodesSelect || onEdgeSelect)}
        onNodeClick={
          onNodeClick
            ? (event, node) => onNodeClick(node.data.node, graphActivation(event))
            : undefined
        }
        onEdgeClick={
          onEdgeClick
            ? (event, edge) => {
                if (edge.data?.edge) onEdgeClick(edge.data.edge, graphActivation(event));
              }
            : undefined
        }
        onNodeDragStop={
          onNodeDragEnd
            ? (_event, node) =>
                onNodeDragEnd(node.data.node, {
                  x: node.position.x,
                  y: node.position.y,
                })
            : undefined
        }
        onConnect={
          onConnect
            ? (connection) => {
                if (!connection.source || !connection.target) return;
                onConnect({
                  source: connection.source,
                  target: connection.target,
                  sourceHandle: connection.sourceHandle,
                  targetHandle: connection.targetHandle,
                });
              }
            : undefined
        }
        onSelectionChange={
          onNodeSelect || onNodesSelect || onEdgeSelect
            ? ({ nodes: selectedNodes, edges: selectedEdges }) => {
                const signature = JSON.stringify([
                  selectedNodes.map((node) => node.id),
                  selectedEdges.map((edge) => edge.id),
                ]);
                if (lastSelectionSignature.current === signature) return;
                lastSelectionSignature.current = signature;
                onNodeSelect?.(selectedNodes[0]?.data.node ?? null);
                onNodesSelect?.(selectedNodes.map((node) => node.data.node));
                onEdgeSelect?.(selectedEdges[0]?.data?.edge ?? null);
              }
            : undefined
        }
      >
        <Background color="var(--border-subtle)" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function graphActivation(event?: React.MouseEvent): GraphViewActivation {
  return { source: event?.detail === 0 ? "keyboard" : "pointer" };
}

function isGraphActivationKey(key: string): boolean {
  return key === "Enter" || key === " ";
}

function toReactFlowNode<
  TKind extends string,
  TMeta extends Record<string, unknown>,
>(
  node: GraphViewNode<TKind, TMeta>,
  nodeStyles: Readonly<Record<TKind, GraphViewNodeStyle>>,
): RenderNode<TKind, TMeta> {
  const style = nodeStyleFor(node.kind, nodeStyles);
  const emphasized = node.selected || node.highlighted;
  return {
    id: node.id,
    domAttributes: { "data-graph-node-id": node.id } as RenderNode<TKind, TMeta>["domAttributes"],
    selected: node.selected,
    ariaLabel: node.ariaLabel,
    type: style.type ?? "default",
    position: { x: 0, y: 0 },
    sourcePosition: Position.Bottom,
    targetPosition: Position.Top,
    data: {
      node,
      label: <GraphNodeLabel node={node} style={style} />,
    },
    style: {
      width: style.width,
      minHeight: style.height,
      borderColor: emphasized
        ? style.highlightedBorderColor ?? "var(--brand)"
        : style.borderColor,
      borderWidth: emphasized ? 2 : 1,
      background: emphasized
        ? style.highlightedBackground ?? "var(--brand-soft)"
        : style.background ?? "var(--surface-sheet)",
      color: style.color ?? "var(--text-primary)",
      padding: 0,
    },
  };
}

function toReactFlowEdge<
  TKind extends string,
  TMeta extends Record<string, unknown>,
>(
  edge: GraphViewEdge<TKind, TMeta>,
  edgeStyles: Readonly<Partial<Record<TKind, GraphViewEdgeStyle>>>,
  defaultEdgeStyle: GraphViewEdgeStyle | undefined,
): RenderEdge<TKind, TMeta> {
  const style = {
    ...DEFAULT_EDGE_STYLE,
    ...defaultEdgeStyle,
    ...edgeStyles[edge.kind],
  };
  return {
    id: edge.id,
    domAttributes: { "data-graph-edge-id": edge.id } as RenderEdge<TKind, TMeta>["domAttributes"],
    selected: edge.selected,
    ariaLabel: edge.ariaLabel,
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    data: { edge },
    label: edge.label,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: {
      stroke: edge.selected ? "var(--brand)" : style.stroke,
      strokeWidth: edge.selected ? Math.max(2, (style.strokeWidth ?? 1) + 1) : style.strokeWidth,
    },
    labelStyle: { fill: style.labelColor, fontSize: 11 },
  };
}

function GraphNodeLabel<TKind extends string>({
  node,
  style,
}: {
  node: GraphViewNode<TKind>;
  style: GraphViewNodeStyle;
}): React.ReactElement {
  return (
    <div className="min-w-0 px-3 py-2 text-left">
      <div className="mb-1 flex min-w-0 items-center justify-between gap-2">
        <span className="truncate text-13 font-semibold text-fg">
          {node.title}
        </span>
        <Badge density="compact" tone={style.badgeTone ?? "neutral"}>
          {node.kindLabel ?? node.kind}
        </Badge>
      </div>
      {node.code ? (
        <Code truncate tone="muted">
          {node.code}
        </Code>
      ) : null}
      {node.detail ? (
        <div className={cn(textRoleVariants({ role: "caption", truncate: true }), "mt-1")}>
          {node.detail}
        </div>
      ) : null}
    </div>
  );
}

function layoutGraph<
  TEdgeKind extends string,
>({
  nodes,
  edges,
  layout,
}: {
  nodes: readonly { id: string; position?: GraphViewPosition; width: number; height: number }[];
  edges: readonly { id: string; source: string; target: string; kind: TEdgeKind }[];
  layout: Required<GraphViewLayout>;
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
  graph.setGraph(layout);

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

function nodeStyleFor<TKind extends string>(
  kind: TKind,
  nodeStyles: Readonly<Record<TKind, GraphViewNodeStyle>>,
): GraphViewNodeStyle {
  const style = nodeStyles[kind];
  if (!style) throw new Error(`Missing graph node style for "${kind}".`);
  return style;
}
