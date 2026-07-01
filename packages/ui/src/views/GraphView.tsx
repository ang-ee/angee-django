/// <reference path="./css.d.ts" />
import * as React from "react";
import * as dagre from "@dagrejs/dagre";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type FitViewOptions,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { cn } from "../lib/cn";
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
  title: React.ReactNode;
  code?: React.ReactNode;
  detail?: React.ReactNode;
  highlighted?: boolean;
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
  label?: React.ReactNode;
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

export interface GraphViewEdgeStyle {
  stroke?: string;
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
  className?: string;
  onNodeClick?: (node: GraphViewNode<TNodeKind, TNodeMeta>) => void;
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
  labelColor: "var(--text-muted)",
};

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
  edgeStyles = {} as Readonly<Partial<Record<TEdgeKind, GraphViewEdgeStyle>>>,
  defaultEdgeStyle,
  layout,
  fitViewOptions = { padding: 0.18 },
  className,
  onNodeClick,
}: GraphViewProps<
  TNodeKind,
  TEdgeKind,
  TNodeMeta,
  TEdgeMeta
>): React.ReactElement {
  const graph = React.useMemo(
    () =>
      layoutGraph({
        nodes: nodes.map((node) => toReactFlowNode(node, nodeStyles)),
        edges: edges.map((edge) =>
          toReactFlowEdge(edge, edgeStyles, defaultEdgeStyle),
        ),
        nodeStyles,
        layout: { ...DEFAULT_LAYOUT, ...layout },
      }),
    [defaultEdgeStyle, edgeStyles, edges, layout, nodeStyles, nodes],
  );

  return (
    <div className={cn("min-h-0", className)}>
      <ReactFlow
        nodes={graph.nodes}
        edges={graph.edges}
        fitView
        fitViewOptions={fitViewOptions}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        onNodeClick={
          onNodeClick
            ? (_, node) => onNodeClick(node.data.node)
            : undefined
        }
      >
        <Background color="var(--border-subtle)" gap={20} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}

function toReactFlowNode<
  TKind extends string,
  TMeta extends Record<string, unknown>,
>(
  node: GraphViewNode<TKind, TMeta>,
  nodeStyles: Readonly<Record<TKind, GraphViewNodeStyle>>,
): RenderNode<TKind, TMeta> {
  const style = nodeStyleFor(node.kind, nodeStyles);
  return {
    id: node.id,
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
      borderColor: node.highlighted
        ? style.highlightedBorderColor ?? "var(--brand)"
        : style.borderColor,
      borderWidth: node.highlighted ? 2 : 1,
      background: node.highlighted
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
    source: edge.source,
    target: edge.target,
    type: "smoothstep",
    data: { edge },
    label: edge.label,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { stroke: style.stroke },
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
          {node.kind}
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
  TNodeKind extends string,
  TEdgeKind extends string,
  TNodeMeta extends Record<string, unknown>,
  TEdgeMeta extends Record<string, unknown>,
>({
  nodes,
  edges,
  nodeStyles,
  layout,
}: {
  nodes: readonly RenderNode<TNodeKind, TNodeMeta>[];
  edges: readonly RenderEdge<TEdgeKind, TEdgeMeta>[];
  nodeStyles: Readonly<Record<TNodeKind, GraphViewNodeStyle>>;
  layout: Required<GraphViewLayout>;
}): {
  nodes: RenderNode<TNodeKind, TNodeMeta>[];
  edges: RenderEdge<TEdgeKind, TEdgeMeta>[];
} {
  const graph = new dagre.graphlib.Graph({ directed: true, multigraph: true });
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph(layout);

  for (const node of nodes) {
    const style = nodeStyleFor(node.data.node.kind, nodeStyles);
    graph.setNode(node.id, { width: style.width, height: style.height });
  }
  for (const edge of edges) {
    graph.setEdge(edge.source, edge.target, { weight: 1 }, edge.id);
  }

  dagre.layout(graph);

  return {
    nodes: nodes.map((node) => {
      const position = graph.node(node.id);
      const style = nodeStyleFor(node.data.node.kind, nodeStyles);
      return {
        ...node,
        position: {
          x: position.x - style.width / 2,
          y: position.y - style.height / 2,
        },
      };
    }),
    edges: [...edges],
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
