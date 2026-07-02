// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { GraphView } from "./GraphView";

const reactFlowMock = vi.hoisted(() => ({
  lastProps: undefined as Record<string, unknown> | undefined,
}));

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  return {
    Background: () => React.createElement("div", { "data-testid": "background" }),
    Controls: () => React.createElement("div", { "data-testid": "controls" }),
    MarkerType: { ArrowClosed: "arrowclosed" },
    Position: { Bottom: "bottom", Top: "top" },
    ReactFlow: (props: Record<string, unknown> & { children?: ReactNode }) => {
      reactFlowMock.lastProps = props;
      return React.createElement(
        "div",
        { "data-testid": "react-flow" },
        props.children,
      );
    },
  };
});

afterEach(() => {
  cleanup();
  reactFlowMock.lastProps = undefined;
});

const nodes = [
  {
    id: "draft",
    kind: "handler",
    title: "Draft",
    code: "handler",
  },
  {
    id: "review",
    kind: "gate",
    title: "Review",
    code: "gate",
  },
] as const;

const edges = [
  {
    id: "draft-review",
    source: "draft",
    target: "review",
    kind: "success",
    label: "success",
  },
] as const;

const nodeStyles = {
  handler: {
    width: 160,
    height: 72,
    borderColor: "var(--border-subtle)",
  },
  gate: {
    width: 160,
    height: 72,
    borderColor: "var(--border-subtle)",
  },
} as const;

function currentProps(): Record<string, unknown> {
  if (!reactFlowMock.lastProps) throw new Error("ReactFlow did not render.");
  return reactFlowMock.lastProps;
}

describe("GraphView", () => {
  test("keeps the canvas read-only by default", () => {
    render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);

    const props = currentProps();
    expect(props.nodesDraggable).toBe(false);
    expect(props.nodesConnectable).toBe(false);
    expect(props.elementsSelectable).toBe(false);
  });

  test("uses persisted node positions before dagre layout positions", () => {
    render(
      <GraphView
        nodes={[
          { ...nodes[0], position: { x: 120, y: 80 } },
          { ...nodes[1], position: { x: 360, y: 140 } },
        ]}
        edges={edges}
        nodeStyles={nodeStyles}
      />,
    );

    const flowNodes = currentProps().nodes as {
      id: string;
      position: { x: number; y: number };
    }[];
    expect(flowNodes.map((node) => [node.id, node.position])).toEqual([
      ["draft", { x: 120, y: 80 }],
      ["review", { x: 360, y: 140 }],
    ]);
  });

  test("adapts editable canvas callbacks to graph records", () => {
    const onNodeDragEnd = vi.fn();
    const onConnect = vi.fn();
    const onNodeSelect = vi.fn();
    const onEdgeSelect = vi.fn();

    render(
      <GraphView
        nodes={nodes}
        edges={edges}
        nodeStyles={nodeStyles}
        nodesDraggable
        onNodeDragEnd={onNodeDragEnd}
        onConnect={onConnect}
        onNodeSelect={onNodeSelect}
        onEdgeSelect={onEdgeSelect}
      />,
    );

    const props = currentProps();
    const flowNodes = props.nodes as {
      id: string;
      position: { x: number; y: number };
      data: { node: (typeof nodes)[number] };
    }[];
    const flowEdges = props.edges as {
      id: string;
      data: { edge: (typeof edges)[number] };
    }[];

    expect(props.nodesDraggable).toBe(true);
    expect(props.nodesConnectable).toBe(true);
    expect(props.elementsSelectable).toBe(true);

    (
      props.onNodeDragStop as (
        event: unknown,
        node: (typeof flowNodes)[number],
      ) => void
    )(undefined, { ...flowNodes[0]!, position: { x: 44, y: 88 } });
    expect(onNodeDragEnd).toHaveBeenCalledWith(nodes[0], { x: 44, y: 88 });

    (props.onConnect as (connection: unknown) => void)({
      source: "draft",
      target: "review",
      sourceHandle: "right",
      targetHandle: null,
    });
    expect(onConnect).toHaveBeenCalledWith({
      source: "draft",
      target: "review",
      sourceHandle: "right",
      targetHandle: null,
    });

    (
      props.onSelectionChange as (selection: {
        nodes: typeof flowNodes;
        edges: typeof flowEdges;
      }) => void
    )({ nodes: [flowNodes[0]!], edges: [] });
    expect(onNodeSelect).toHaveBeenLastCalledWith(nodes[0]);
    expect(onEdgeSelect).toHaveBeenLastCalledWith(null);

    (
      props.onSelectionChange as (selection: {
        nodes: typeof flowNodes;
        edges: typeof flowEdges;
      }) => void
    )({ nodes: [], edges: [flowEdges[0]!] });
    expect(onNodeSelect).toHaveBeenLastCalledWith(null);
    expect(onEdgeSelect).toHaveBeenLastCalledWith(edges[0]);
  });
});
