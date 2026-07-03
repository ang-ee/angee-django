import * as React from "react";
import type { Meta, StoryObj } from "@storybook/react-vite";
import {
  GraphView,
  type GraphViewConnection,
  type GraphViewEdge,
  type GraphViewNode,
  type GraphViewNodeStyle,
} from "@angee/ui";

type NodeKind = "handler" | "gate" | "publish";
type EdgeKind = "default" | "success" | "reject";

const nodeStyles = {
  handler: {
    width: 180,
    height: 76,
    borderColor: "var(--border-subtle)",
    badgeTone: "brand",
  },
  gate: {
    width: 180,
    height: 76,
    borderColor: "var(--warning)",
    background: "var(--warning-soft)",
    badgeTone: "warning",
  },
  publish: {
    width: 180,
    height: 76,
    borderColor: "var(--success)",
    background: "var(--success-soft)",
    badgeTone: "success",
  },
} satisfies Record<NodeKind, GraphViewNodeStyle>;

const initialNodes = [
  {
    id: "draft",
    kind: "handler",
    title: "Prepare Draft",
    code: "handler",
    detail: "Build the candidate payload.",
  },
  {
    id: "review",
    kind: "gate",
    title: "Review",
    code: "gate",
    detail: "Wait for a human decision.",
  },
  {
    id: "publish",
    kind: "publish",
    title: "Publish",
    code: "handler",
    detail: "Finalize the workflow output.",
  },
] satisfies GraphViewNode<NodeKind>[];

const initialEdges = [
  {
    id: "draft-review",
    source: "draft",
    target: "review",
    kind: "success",
    label: "ready",
  },
  {
    id: "review-publish",
    source: "review",
    target: "publish",
    kind: "success",
    label: "approved",
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

  const updateNodePosition = React.useCallback(
    (node: GraphViewNode<NodeKind>, position: { x: number; y: number }) => {
      setNodes((current) =>
        current.map((entry) =>
          entry.id === node.id ? { ...entry, position } : entry,
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
        onNodeDragEnd={updateNodePosition}
        onConnect={createEdge}
        onNodeSelect={(node) => setSelected(node?.title?.toString() ?? "None")}
        onEdgeSelect={(edge) => setSelected(edge?.id ?? "None")}
      />
    </div>
  );
}
