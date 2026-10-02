/// <reference path="./css.d.ts" />
import * as React from "react";
import {
  Background,
  Controls,
  MiniMap,
  getIncomers,
  getViewportForBounds,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  Handle,
  useUpdateNodeInternals,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type Edge,
  type FitViewOptions,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { DEFAULT_GRAPH_LAYOUT as DEFAULT_LAYOUT, layoutGraph } from "./graph-layout";
import { useUiT } from "../i18n";
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
  /** Named output handles; omitted preserves the default unnamed output. */
  ports?: readonly GraphViewPort[];
  meta?: TMeta;
}

export interface GraphViewEdge<
  TKind extends string = string,
  TMeta extends Record<string, unknown> = Record<string, unknown>,
> {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  kind: TKind;
  ariaLabel?: string;
  label?: React.ReactNode;
  /** Controlled native React Flow selection state. */
  selected?: boolean;
  meta?: TMeta;
}

export interface GraphViewPort {
  id: string;
  label?: string;
}

export interface GraphViewStatus {
  label: React.ReactNode;
  tone?: Tone;
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

export interface GraphViewInitialView {
  /** Defer the initial viewport while an asynchronous graph projection is incomplete. */
  ready?: boolean;
  /** Center this node along the flow axis; defaults to a source node, then the first node. */
  anchorNodeId?: string;
  /** Readable initial zoom floor. Does not restrict subsequent whole-graph fits. Defaults to 0.65. */
  minZoom?: number;
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
  status?: Readonly<Record<string, GraphViewStatus | undefined>>;
  fitViewOptions?: FitViewOptions;
  /** Change this token to request another fit after the initial measured fit. */
  fitViewRequest?: number;
  /** Initially fit the graph's cross-axis and anchor its flow-axis, using measured native bounds. */
  initialView?: GraphViewInitialView;
  /** Native overview with pan and zoom navigation. */
  miniMap?: boolean;
  /** Accessible name for the focusable graph surface. */
  ariaLabel?: string;
  className?: string;
  onNodeClick?: (node: GraphViewNode<TNodeKind, TNodeMeta>, activation: GraphViewActivation) => void;
  onEdgeClick?: (edge: GraphViewEdge<TEdgeKind, TEdgeMeta>, activation: GraphViewActivation) => void;
  nodesDraggable?: boolean;
  onConnect?: (edge: GraphViewConnection) => void;
  isValidConnection?: (edge: GraphViewConnection) => boolean;
  onReconnect?: (edge: GraphViewEdge<TEdgeKind, TEdgeMeta>, connection: GraphViewConnection) => void;
  /** Reports settled pointer and keyboard positions in one batch to the controlled owner. */
  onNodesPositionChange?: (positions: Readonly<Record<string, GraphViewPosition>>) => void;
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
  style: GraphViewNodeStyle;
  status?: GraphViewStatus;
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
const DEFAULT_FIT_VIEW_OPTIONS: FitViewOptions = { padding: 0.18, minZoom: 0.05 };
const HANDLE_POSITIONS = {
  TB: { source: Position.Bottom, target: Position.Top },
  BT: { source: Position.Top, target: Position.Bottom },
  LR: { source: Position.Right, target: Position.Left },
  RL: { source: Position.Left, target: Position.Right },
};

const NODE_TYPES = { angee: GraphNode };

export function GraphView<
  TNodeKind extends string = string,
  TEdgeKind extends string = string,
  TNodeMeta extends Record<string, unknown> = Record<string, unknown>,
  TEdgeMeta extends Record<string, unknown> = Record<string, unknown>,
>(props: GraphViewProps<TNodeKind, TEdgeKind, TNodeMeta, TEdgeMeta>): React.ReactElement {
  return <ReactFlowProvider><GraphCanvas {...props} /></ReactFlowProvider>;
}

function GraphCanvas<
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
  status,
  fitViewOptions = DEFAULT_FIT_VIEW_OPTIONS,
  fitViewRequest = 0,
  initialView,
  miniMap = false,
  ariaLabel,
  className,
  onNodeClick,
  onEdgeClick,
  nodesDraggable = false,
  onConnect,
  isValidConnection,
  onReconnect,
  onNodesPositionChange,
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
  // Only transient selection/dragging is local; records and settled positions stay controlled.
  const [selection, setSelection] = React.useState<Readonly<Record<string, boolean>>>({});
  const [edgeSelection, setEdgeSelection] = React.useState<Readonly<Record<string, boolean>>>({});
  const [dragging, setDragging] = React.useState<Readonly<Record<string, GraphViewPosition>>>({});
  const [measured, setMeasured] = React.useState<Readonly<Record<string, { width: number; height: number }>>>({});
  const nodeSet = JSON.stringify(nodes.map((node) => node.id).sort());
  const edgeSet = JSON.stringify(edges.map((edge) => edge.id).sort());
  const [previousNodeSet, setPreviousNodeSet] = React.useState(nodeSet);
  const [previousEdgeSet, setPreviousEdgeSet] = React.useState(edgeSet);
  // A changed data set starts a new local selection; retained nodes keep their measurements.
  if (nodeSet !== previousNodeSet) {
    setPreviousNodeSet(nodeSet);
    setSelection({});
    setDragging({});
    setMeasured(Object.fromEntries(nodes.flatMap((node) => {
      const dimensions = measured[node.id];
      return dimensions ? [[node.id, dimensions] as const] : [];
    })));
  }
  if (edgeSet !== previousEdgeSet) {
    setPreviousEdgeSet(edgeSet);
    setEdgeSelection({});
  }
  const nodeCache = React.useRef(new Map<string, RenderNode<TNodeKind, TNodeMeta>>());
  const renderNodes = React.useMemo(() => {
    const next = new Map<string, RenderNode<TNodeKind, TNodeMeta>>();
    const result = nodes.map((node) => {
      const selected = node.selected ?? selection[node.id] ?? false;
      const position = dragging[node.id] ?? node.position ?? geometryLayout.positions.get(node.id)!;
      const previous = nodeCache.current.get(node.id);
      const style = resolvedNodeStyles[node.kind];
      const overlay = status?.[node.id];
      if (previous && sameNodeContent(previous.data.node, node)
        && previous.data.style === style && previous.data.status === overlay
        && previous.sourcePosition === HANDLE_POSITIONS[rankdir].source
        && previous.targetPosition === HANDLE_POSITIONS[rankdir].target
        && previous.selected === selected && previous.measured === measured[node.id]
        && previous.position.x === position.x && previous.position.y === position.y) {
        next.set(node.id, previous);
        return previous;
      }
      const rendered = {
        ...toReactFlowNode(node, resolvedNodeStyles, HANDLE_POSITIONS[rankdir], overlay, selected),
        position, measured: measured[node.id],
      };
      next.set(node.id, rendered);
      return rendered;
    });
    nodeCache.current = next;
    return result;
  }, [nodes, resolvedNodeStyles, status, selection, dragging, measured, geometryLayout, rankdir]);
  // Declared handles are authoritative. An impossible link cannot stall other
  // edges or fitting, even if React Flow retains bounds from an older render.
  const invalidEdges = React.useMemo(() => edges.filter((edge) => {
    const source = nodes.find((node) => node.id === edge.source);
    const target = nodes.find((node) => node.id === edge.target);
    if (!source || !target) return true;
    const sourceExists = source.ports
      ? source.ports.some((port) => port.id === (edge.sourceHandle ?? null))
      : (edge.sourceHandle ?? null) === null && nodeStyles[source.kind].type !== "output";
    return !sourceExists || (edge.targetHandle ?? null) !== null || nodeStyles[target.kind].type === "input";
  }), [edges, nodes, nodeStyles]);
  const warnedEdges = React.useRef(new Set<string>());
  React.useEffect(() => {
    for (const edge of invalidEdges) {
      if (warnedEdges.current.has(edge.id)) continue;
      warnedEdges.current.add(edge.id);
      console.warn(`GraphView excluded edge "${edge.id}": its declared source or target handle does not exist.`);
    }
  }, [invalidEdges]);
  const renderEdges = React.useMemo(() => edges
    .filter((edge) => geometryLayout.visibleEdgeIds.has(edge.id) && !invalidEdges.includes(edge))
    .map((edge) => ({
      ...toReactFlowEdge(edge, resolvedEdgeStyles, resolvedDefaultEdgeStyle),
      selected: edge.selected ?? edgeSelection[edge.id] ?? false,
    })), [edges, geometryLayout, invalidEdges, resolvedDefaultEdgeStyle, resolvedEdgeStyles, edgeSelection]);
  // A primitive selector stays stable when unrelated store state changes. Each
  // link waits only for its own measured source and target bounds.
  const readyEdges = useStore((state) => renderEdges.map((edge) => {
    const source = state.nodeLookup.get(edge.source)?.internals.handleBounds?.source;
    const target = state.nodeLookup.get(edge.target)?.internals.handleBounds?.target;
    return source?.some((handle) => (handle.id ?? null) === (edge.sourceHandle ?? null))
      && target?.some((handle) => (handle.id ?? null) === (edge.targetHandle ?? null)) ? "1" : "0";
  }).join(""));
  const initialized = useNodesInitialized();
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  const { fitView, getNode, getEdge, getNodes, getNodesBounds, setViewport, viewportInitialized } = useReactFlow<RenderNode<TNodeKind, TNodeMeta>, RenderEdge<TEdgeKind, TEdgeMeta>>();
  const fittedRequest = React.useRef<number | undefined>(undefined);
  React.useEffect(() => {
    if (!initialized || fittedRequest.current === fitViewRequest) return;
    if (fittedRequest.current === undefined && initialView) {
      if (initialView.ready === false || !viewportInitialized || width <= 0 || height <= 0) return;
      const measuredNodes = getNodes();
      const anchor = measuredNodes.find((node) => node.id === initialView.anchorNodeId)
        ?? measuredNodes.find((node) => getIncomers(node, measuredNodes, renderEdges).length === 0)
        ?? measuredNodes[0];
      if (!anchor) return;
      const bounds = getNodesBounds(measuredNodes);
      const anchorBounds = getNodesBounds([anchor]);
      const horizontal = rankdir === "LR" || rankdir === "RL";
      // Zero extent on the flow axis lets the native fitter consider only the
      // cross-axis. Its center on that axis remains the chosen node's center.
      const crossAxisBounds = horizontal
        ? { ...bounds, x: anchorBounds.x + anchorBounds.width / 2, width: 0 }
        : { ...bounds, y: anchorBounds.y + anchorBounds.height / 2, height: 0 };
      const padding = fitViewOptions.padding ?? 0.18;
      const viewport = getViewportForBounds(crossAxisBounds, width, height,
        initialView.minZoom ?? 0.65, fitViewOptions.maxZoom ?? 1, padding);
      // If the readable floor crops even the cross-axis, keep the anchor in
      // view rather than centering a large branch extent away from its entry.
      const cropped = horizontal ? bounds.height * viewport.zoom > height : bounds.width * viewport.zoom > width;
      void setViewport(cropped
        ? getViewportForBounds(anchorBounds, width, height, viewport.zoom, viewport.zoom, padding)
        : viewport);
    } else {
      void fitView({ ...DEFAULT_FIT_VIEW_OPTIONS, ...fitViewOptions });
    }
    fittedRequest.current = fitViewRequest;
  }, [initialized, viewportInitialized, width, height, fitViewRequest, fitView, fitViewOptions, initialView, getNodes, getNodesBounds, setViewport, rankdir, renderEdges]);
  // React Flow re-emits selection state whenever its store adopts replaced
  // nodes. Consumers set state from these callbacks, so re-emitting an
  // unchanged selection loops: setState → re-render → store resync → re-emit
  // ("Maximum update depth exceeded" in StoreUpdater). Emit only on change.
  const lastNodeSelection = React.useRef<string | null>(null);
  const lastEdgeSelection = React.useRef<string | null>(null);
  const controlledNodeSelection = nodes.some((node) => node.selected !== undefined);
  const controlledEdgeSelection = edges.some((edge) => edge.selected !== undefined);
  function emitNodeSelection(selected: readonly GraphViewNode<TNodeKind, TNodeMeta>[]): void {
    const signature = JSON.stringify(selected.map((node) => node.id));
    if (!controlledNodeSelection && lastNodeSelection.current === signature) return;
    lastNodeSelection.current = signature;
    onNodesSelect?.(selected);
  }
  function emitEdgeSelection(selected: readonly GraphViewEdge<TEdgeKind, TEdgeMeta>[]): void {
    const signature = JSON.stringify(selected.map((edge) => edge.id));
    if (!controlledEdgeSelection && lastEdgeSelection.current === signature) return;
    lastEdgeSelection.current = signature;
    onEdgeSelect?.(selected[0] ?? null);
  }

  return (
    <div
      tabIndex={-1}
      role={ariaLabel ? "region" : undefined}
      aria-label={ariaLabel}
      className={cn("relative min-h-0 outline-none", className)}
    >
      {/* React Flow sizes its root to 100% of its parent, which collapses to
          zero under a min-height or flexed wrapper; an absolutely filled box
          gives it a definite size however the caller sizes the wrapper. */}
      <div className="absolute inset-0">
        <ReactFlow<RenderNode<TNodeKind, TNodeMeta>, RenderEdge<TEdgeKind, TEdgeMeta>>
          nodeTypes={NODE_TYPES}
          deleteKeyCode={null}
          nodes={renderNodes}
          edges={renderEdges.filter((_edge, index) => readyEdges[index] === "1")}
          onKeyDown={(event) => {
            if (!isGraphActivationKey(event.key)) return;
            if (!(event.target instanceof Element)) return;
            const nodeId = event.target.getAttribute("data-graph-node-id");
            const edgeId = event.target.getAttribute("data-graph-edge-id");
            if (nodeId) {
              const node = getNode(nodeId);
              if (node) {
                event.preventDefault();
                onNodeClick?.(node.data.node, { source: "keyboard" });
              }
            } else if (edgeId) {
              const edge = getEdge(edgeId);
              if (edge?.data?.edge) {
                event.preventDefault();
                onEdgeClick?.(edge.data.edge, { source: "keyboard" });
              }
            }
          }}
          onNodesChange={(changes) => {
            const positions: Record<string, GraphViewPosition> = {};
            const nextSelection = new Map(renderNodes.map((node) => [node.id, node.selected]));
            let selectionChanged = false;
            for (const change of changes) {
              if (change.type === "select") {
                setSelection((current) => ({ ...current, [change.id]: change.selected }));
                nextSelection.set(change.id, change.selected);
                selectionChanged = true;
              }
              if (change.type === "position" && change.position) {
                if (!change.dragging) positions[change.id] = change.position;
                setDragging((current) => {
                  const next = { ...current };
                  if (change.dragging) next[change.id] = change.position!;
                  else delete next[change.id];
                  return next;
                });
              }
              if (change.type === "dimensions" && change.dimensions) {
                const dimensions = change.dimensions;
                setMeasured((current) => current[change.id]?.width === dimensions.width
                  && current[change.id]?.height === dimensions.height
                  ? current : { ...current, [change.id]: dimensions });
              }
            }
            if (Object.keys(positions).length) onNodesPositionChange?.(positions);
            if (selectionChanged && controlledNodeSelection) emitNodeSelection(nodes.filter((node) => nextSelection.get(node.id)));
          }}
          onEdgesChange={(changes) => {
            const nextSelection = new Map(renderEdges.map((edge) => [edge.id, edge.selected]));
            let selectionChanged = false;
            for (const change of changes) {
              if (change.type === "select") {
                setEdgeSelection((current) => ({ ...current, [change.id]: change.selected }));
                nextSelection.set(change.id, change.selected);
                selectionChanged = true;
              }
            }
            if (selectionChanged && controlledEdgeSelection) emitEdgeSelection(edges.filter((edge) => nextSelection.get(edge.id)));
          }}
          minZoom={fitViewOptions.minZoom ?? DEFAULT_FIT_VIEW_OPTIONS.minZoom}
          fitView={false}
          fitViewOptions={fitViewOptions}
          nodesDraggable={nodesDraggable}
          nodesConnectable={Boolean(onConnect)}
          elementsSelectable={Boolean(onNodesSelect || onEdgeSelect)}
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
          isValidConnection={isValidConnection}
          edgesReconnectable={Boolean(onReconnect)}
          onReconnect={onReconnect ? (edge, connection) => {
            if (edge.data?.edge && (!isValidConnection || isValidConnection(connection))) onReconnect(edge.data.edge, connection);
          } : undefined}
          onConnect={
            onConnect
              ? (connection) => {
                  if (!connection.source || !connection.target || (isValidConnection && !isValidConnection(connection))) return;
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
            onNodesSelect || onEdgeSelect
              ? ({ nodes: selectedNodes, edges: selectedEdges }) => {
                  if (!controlledNodeSelection) emitNodeSelection(selectedNodes.map((node) => node.data.node));
                  if (!controlledEdgeSelection) emitEdgeSelection(selectedEdges.flatMap((edge) => edge.data ? [edge.data.edge] : []));
                }
              : undefined
          }
        >
          <Background color="var(--border-subtle)" gap={20} />
          <Controls showInteractive={false} />
          {miniMap ? <MiniMap pannable zoomable /> : null}
        </ReactFlow>
      </div>
      <div role="status" className="sr-only">
        {nodes.filter((node) => status?.[node.id]).map((node) => (
          <span key={node.id}>{node.ariaLabel ?? (typeof node.title === "string" ? node.title : node.id)}: {status?.[node.id]?.label}. </span>
        ))}
      </div>
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
  positions: { source: Position; target: Position },
  status?: GraphViewStatus,
  selected = node.selected,
): RenderNode<TKind, TMeta> {
  const style = nodeStyleFor(node.kind, nodeStyles);
  const emphasized = selected || node.highlighted;
  return {
    id: node.id,
    domAttributes: { "data-graph-node-id": node.id } as RenderNode<TKind, TMeta>["domAttributes"],
    selected,
    ariaLabel: node.ariaLabel,
    type: "angee",
    position: { x: 0, y: 0 },
    sourcePosition: positions.source,
    targetPosition: positions.target,
    data: {
      node,
      label: <GraphNodeLabel node={node} style={style} status={status} />,
      style,
      status,
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
      borderStyle: "solid",
      borderRadius: 6,
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
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
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
  status,
}: {
  node: GraphViewNode<TKind>;
  style: GraphViewNodeStyle;
  status?: GraphViewStatus;
}): React.ReactElement {
  return (
    <div className="relative min-w-0 px-3 py-2 text-left">
      <div className="mb-1 flex min-w-0 items-center justify-between gap-2">
        <span className="truncate text-13 font-semibold text-fg">
          {node.title}
        </span>
        <Badge density="compact" tone={style.badgeTone ?? "neutral"}>
          {node.kindLabel ?? node.kind}
        </Badge>
      </div>
      {status ? <Badge className="absolute -top-3 right-2" tone={status.tone ?? "neutral"} density="compact">{status.label}</Badge> : null}
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


function GraphNode({
  id, data, isConnectable, sourcePosition = Position.Bottom, targetPosition = Position.Top,
}: NodeProps<RenderNode<string, Record<string, unknown>>>): React.ReactElement {
  const t = useUiT();
  const updateNodeInternals = useUpdateNodeInternals();
  const portsSignature = JSON.stringify(data.node.ports?.map((port) => port.id));
  const horizontal = sourcePosition === Position.Right || sourcePosition === Position.Left;
  React.useEffect(() => { updateNodeInternals(id); }, [id, portsSignature, data.style.type, sourcePosition, targetPosition, updateNodeInternals]);
  return (
    <>
      {data.style.type !== "input" ? <Handle type="target" position={targetPosition} isConnectable={isConnectable} aria-label={t("graph.input")} /> : null}
      {data.label}
      {data.node.ports ? <div className={cn("flex gap-1 px-2 pb-2 text-2xs text-fg-muted", horizontal ? "flex-col" : "justify-around")}>
        {data.node.ports.map((port, index, ports) => <span key={port.id} className={horizontal ? cn("relative w-full", sourcePosition === Position.Right ? "text-right" : "text-left") : undefined}>
          {port.label ?? port.id}
          <Handle id={port.id} type="source" position={sourcePosition} isConnectable={isConnectable} aria-label={port.label ?? port.id} style={horizontal
            ? { top: "50%", [sourcePosition === Position.Right ? "right" : "left"]: -8 }
            : { left: ((index + 1) / (ports.length + 1)) * 100 + "%" }} />
        </span>)}
      </div> : data.style.type !== "output" ? <Handle type="source" position={sourcePosition} isConnectable={isConnectable} aria-label={t("graph.output")} /> : null}
    </>
  );
}

function nodeStyleFor<TKind extends string>(
  kind: TKind,
  nodeStyles: Readonly<Record<TKind, GraphViewNodeStyle>>,
): GraphViewNodeStyle {
  const style = nodeStyles[kind];
  if (!style) throw new Error(`Missing graph node style for "${kind}".`);
  return style;
}

function sameNodeContent(left: GraphViewNode, right: GraphViewNode): boolean {
  return left.kind === right.kind && left.kindLabel === right.kindLabel
    && left.title === right.title && left.code === right.code && left.detail === right.detail
    && left.ariaLabel === right.ariaLabel && left.highlighted === right.highlighted
    && left.meta === right.meta && left.ports === right.ports
    && left.selected === right.selected && left.position?.x === right.position?.x && left.position?.y === right.position?.y;
}
