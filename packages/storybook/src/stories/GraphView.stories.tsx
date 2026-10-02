import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  GraphView,
  type GraphViewConnection,
  type GraphViewEdge,
  type GraphViewNode,
  type GraphViewNodeStyle,
} from "@angee/ui";

type NodeKind = "node" | "junction" | "terminal";
type EdgeKind = "default" | "primary" | "secondary";

const nodeStyles = {
  node: {
    width: 180,
    height: 100,
    borderColor: "var(--border-subtle)",
    badgeTone: "brand",
  },
  junction: {
    width: 180,
    height: 100,
    borderColor: "var(--warning)",
    background: "var(--warning-soft)",
    badgeTone: "warning",
  },
  terminal: {
    width: 180,
    height: 100,
    borderColor: "var(--success)",
    background: "var(--success-soft)",
    badgeTone: "success",
  },
} satisfies Record<NodeKind, GraphViewNodeStyle>;

const initialNodes = [
  {
    id: "a",
    kind: "node",
    title: "Alpha",
    detail: "First node.",
    ports: [{ id: "next", label: "Next" }],
  },
  {
    id: "b",
    kind: "junction",
    title: "Beta",
    detail: "Middle node.",
    ports: [{ id: "next", label: "Next" }],
  },
  {
    id: "c",
    kind: "terminal",
    title: "Gamma",
    detail: "Last node.",
    ports: [],
  },
] satisfies GraphViewNode<NodeKind>[];

const initialEdges = [
  {
    id: "a-b",
    source: "a",
    sourceHandle: "next",
    target: "b",
    kind: "primary",
    label: "First link",
  },
  {
    id: "b-c",
    source: "b",
    sourceHandle: "next",
    target: "c",
    kind: "primary",
    label: "Second link",
  },
] satisfies GraphViewEdge<EdgeKind>[];

const meta = {
  title: "Views/GraphView",
  component: GraphView,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof GraphView>;

export default meta;

type Story = StoryObj;

export const ReadOnly: Story = {
  render: () => (
    <div className="h-screen bg-canvas p-6">
      <GraphView
        className="h-full rounded-6 border border-border-subtle bg-sheet-1"
        nodes={initialNodes}
        edges={initialEdges}
        nodeStyles={nodeStyles}
        status={{ b: { label: "Selected", tone: "info" } }}
      />
    </div>
  ),
};

export const Editable: Story = {
  render: () => <EditableGraphFixture />,
};

function EditableGraphFixture(): React.ReactElement {
  const [nodes, setNodes] =
    React.useState<GraphViewNode<NodeKind>[]>(initialNodes);
  const [edges, setEdges] =
    React.useState<GraphViewEdge<EdgeKind>[]>(initialEdges);
  const [selected, setSelected] = React.useState<string>("None");

  const updateNodePositions = React.useCallback(
    (positions: Readonly<Record<string, { x: number; y: number }>>) => {
      setNodes((current) =>
        current.map((entry) =>
          positions[entry.id] ? { ...entry, position: positions[entry.id] } : entry,
        ),
      );
    },
    [],
  );
  const createEdge = React.useCallback((connection: GraphViewConnection) => {
    setEdges((current) => [
      ...current,
      {
        id: `${connection.source}-${connection.target}-${current.length}`,
        source: connection.source,
        target: connection.target,
        sourceHandle: connection.sourceHandle,
        targetHandle: connection.targetHandle,
        kind: "default",
      },
    ]);
  }, []);

  return (
    <div className="flex h-screen flex-col bg-canvas text-fg">
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-border-subtle bg-sheet-1 px-4 text-13">
        <span className="font-medium">Editable canvas</span>
        <span className="text-fg-muted">Selected: {selected}</span>
      </div>
      <GraphView
        className="min-h-0 flex-1"
        nodes={nodes}
        edges={edges}
        nodeStyles={nodeStyles}
        nodesDraggable
        onNodesPositionChange={updateNodePositions}
        onConnect={createEdge}
        onNodesSelect={(nodes) => setSelected(nodes.map((node) => node.title).join(", "))}
        onEdgeSelect={(edge) => setSelected(edge?.id ?? "None")}
      />
    </div>
  );
}
