// @vitest-environment happy-dom

import { act, cleanup, render } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { GraphView, graphNodeStyle } from "./GraphView";

const reactFlowMock = vi.hoisted(() => ({
  lastProps: undefined as Record<string, unknown> | undefined,
  initialized: true,
  handlesReady: true,
  fitView: vi.fn(),
}));
const dagreMock = vi.hoisted(() => ({ layouts: 0 }));

vi.mock("@dagrejs/dagre", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dagrejs/dagre")>();
  return {
    ...actual,
    layout: (graph: Parameters<typeof actual.layout>[0], options?: Parameters<typeof actual.layout>[1]) => {
      dagreMock.layouts += 1;
      return actual.layout(graph, options);
    },
  };
});

vi.mock("@xyflow/react", async () => {
  const React = await import("react");
  return {
    Background: () => React.createElement("div", { "data-testid": "background" }),
    Controls: () => React.createElement("div", { "data-testid": "controls" }),
    MarkerType: { ArrowClosed: "arrowclosed" },
    Position: { Bottom: "bottom", Top: "top" },
    ReactFlow: (props: Record<string, unknown> & { children?: ReactNode }) => {
      reactFlowMock.lastProps = props;
      const nodes = props.nodes as Array<{ id: string }>;
      (props.onInit as ((instance: object) => void) | undefined)?.({
        getNode: (id: string) => nodes.find((node) => node.id === id),
        getEdge: (id: string) => (props.edges as Array<{ id: string }>).find((edge) => edge.id === id),
      });
      return React.createElement(
        "div",
        { "data-testid": "react-flow" },
        props.children,
      );
    },
    ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
    useNodesInitialized: () => reactFlowMock.initialized,
    useReactFlow: () => ({ fitView: reactFlowMock.fitView }),
    useStore: (selector: (state: unknown) => unknown) => selector({ nodeLookup: { get: () => ({ internals: {
      handleBounds: reactFlowMock.handlesReady ? { source: [{ id: null }, { id: "done" }], target: [{ id: null }] } : undefined,
    } }) } }),
  };
});

afterEach(() => {
  cleanup();
  reactFlowMock.lastProps = undefined;
  dagreMock.layouts = 0;
  reactFlowMock.initialized = true;
  reactFlowMock.handlesReady = true;
  reactFlowMock.fitView.mockClear();
});

test("edges and initial fit wait for native initialization and current handle bounds", () => {
  reactFlowMock.initialized = false;
  const graph = () => <GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />;
  const view = render(graph());
  expect(reactFlowMock.lastProps?.edges).toEqual([]);
  expect(reactFlowMock.lastProps?.fitView).toBe(false);
  expect(reactFlowMock.fitView).not.toHaveBeenCalled();
  reactFlowMock.initialized = true;
  reactFlowMock.handlesReady = false;
  view.rerender(graph());
  expect(reactFlowMock.lastProps?.edges).toEqual([]);
  expect(reactFlowMock.fitView).not.toHaveBeenCalled();
  reactFlowMock.handlesReady = true;
  view.rerender(graph());
  expect(reactFlowMock.lastProps?.edges).toHaveLength(1);
  expect(reactFlowMock.fitView).toHaveBeenCalledOnce();
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
  test("projects graph theme variables with shared dimensions and overrides", () => {
    expect(graphNodeStyle("var(--info)", "info", { background: "var(--info-soft)" })).toEqual({
      width: 188,
      height: 76,
      borderColor: "var(--info)",
      highlightedBorderColor: "var(--brand)",
      badgeTone: "info",
      background: "var(--info-soft)",
    });
    expect(graphNodeStyle("red", "danger", { width: 190, height: 78 }).width).toBe(190);
  });
  test("updates presentation without laying out until semantic geometry changes", () => {
    const rendered = render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);
    const initialLayouts = dagreMock.layouts;
    rendered.rerender(<GraphView
      nodes={nodes.map((node) => node.id === "draft" ? { ...node, title: "Draft renamed" } : node)}
      edges={edges.map((edge) => ({ ...edge, label: "renamed outcome" }))}
      nodeStyles={{ ...nodeStyles }}
      edgeStyles={{ success: { stroke: "purple" } }}
    />);
    expect(dagreMock.layouts).toBe(initialLayouts);
    const renderedNodes = reactFlowMock.lastProps?.nodes as Array<{ data: { node: { title: string } } }>;
    expect(renderedNodes[0]?.data.node.title).toBe("Draft renamed");

    rendered.rerender(<GraphView
      nodes={[...nodes, { id: "publish", kind: "handler", title: "Publish" }]}
      edges={edges}
      nodeStyles={nodeStyles}
    />);
    expect(dagreMock.layouts).toBeGreaterThan(initialLayouts);
  });
  test("forwards accessible names and keeps kind as the style key", () => {
    render(
      <GraphView
        nodes={[{ ...nodes[0], kindLabel: "Operation", ariaLabel: "Draft operation, entry step" }]}
        edges={[{ ...edges[0], source: "draft", target: "draft", ariaLabel: "Draft succeeds to itself" }]}
        nodeStyles={nodeStyles}
      />,
    );

    const flowNode = (currentProps().nodes as Array<{ ariaLabel?: string; data: { label: ReactNode }; style: { width: number } }>)[0]!;
    const flowEdge = (currentProps().edges as Array<{ ariaLabel?: string }>)[0]!;
    expect(flowNode.ariaLabel).toBe("Draft operation, entry step");
    expect(flowNode.style.width).toBe(nodeStyles.handler.width);
    expect(flowEdge.ariaLabel).toBe("Draft succeeds to itself");
    const label = render(flowNode.data.label);
    expect(label.getByText("Operation")).toBeTruthy();
    expect(label.getByText("handler").tagName).toBe("CODE");
  });

  test("keeps the canvas read-only by default", () => {
    render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);

    const props = currentProps();
    expect(props.nodesDraggable).toBe(false);
    expect(props.nodesConnectable).toBe(false);
    expect(props.elementsSelectable).toBe(false);
    expect(props.deleteKeyCode).toBe(null);
  });

  test("ignores native remove intent and reports multi-node positions in one batch", () => {
    const onNodesPositionChange = vi.fn();
    render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} onNodesPositionChange={onNodesPositionChange} />);
    act(() => (currentProps().onNodesChange as (changes: unknown[]) => void)([
      { type: "remove", id: "draft" },
      { type: "position", id: "draft", position: { x: 1, y: 2 } },
      { type: "position", id: "review", position: { x: 3, y: 4 } },
    ]));
    expect(currentProps().nodes).toHaveLength(2);
    expect(onNodesPositionChange).toHaveBeenCalledExactlyOnceWith({ draft: { x: 1, y: 2 }, review: { x: 3, y: 4 } });
  });

  test("reports one settled drag batch and does not run layout for positions", () => {
    const changed = vi.fn();
    const view = render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} onNodesPositionChange={changed} />);
    const layouts = dagreMock.layouts;
    for (const x of [10, 20, 30]) {
      act(() => (currentProps().onNodesChange as (changes: unknown[]) => void)([
        { type: "position", id: "draft", position: { x, y: 40 }, dragging: true },
      ]));
    }
    expect(changed).not.toHaveBeenCalled();
    act(() => (currentProps().onNodesChange as (changes: unknown[]) => void)([
      { type: "position", id: "draft", position: { x: 30, y: 40 }, dragging: false },
    ]));
    expect(changed).toHaveBeenCalledExactlyOnceWith({ draft: { x: 30, y: 40 } });
    view.rerender(<GraphView nodes={nodes.map((node) => ({ ...node, position: { x: 30, y: 40 } }))} edges={edges} nodeStyles={nodeStyles} />);
    expect(dagreMock.layouts).toBe(layouts);
  });

  test("clears uncontrolled selection when the node set changes, including reused IDs", () => {
    const view = render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);
    act(() => (currentProps().onNodesChange as (changes: unknown[]) => void)([{ type: "select", id: "draft", selected: true }]));
    expect(currentProps().nodes).toEqual(expect.arrayContaining([expect.objectContaining({ id: "draft", selected: true })]));
    view.rerender(<GraphView nodes={[nodes[1]]} edges={[]} nodeStyles={nodeStyles} />);
    view.rerender(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);
    expect(currentProps().nodes).toEqual(expect.arrayContaining([expect.objectContaining({ id: "draft", selected: false })]));
  });

  test("lays out self-loops and dangling edges instead of crashing", () => {
    render(
      <GraphView
        nodes={nodes}
        edges={[
          ...edges,
          {
            id: "draft-self",
            source: "draft",
            target: "draft",
            kind: "success",
            label: "self",
          },
          {
            id: "draft-ghost",
            source: "draft",
            target: "ghost",
            kind: "success",
            label: "dangling",
          },
        ]}
        nodeStyles={nodeStyles}
      />,
    );

    const props = currentProps();
    const flowNodes = props.nodes as { id: string }[];
    const flowEdges = props.edges as { id: string }[];
    // Dagre positions both declared nodes; the self-loop still renders
    // (React Flow draws it without dagre); the dangling edge is dropped —
    // React Flow could not draw an edge to a node that does not exist.
    expect(flowNodes.map((node) => node.id)).toEqual(["draft", "review"]);
    expect(flowEdges.map((edge) => edge.id)).toEqual([
      "draft-review",
      "draft-self",
    ]);
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
    const onConnect = vi.fn();
    const onNodesSelect = vi.fn();
    const onEdgeSelect = vi.fn();
    const onEdgeClick = vi.fn();

    render(
      <GraphView
        nodes={nodes}
        edges={edges}
        nodeStyles={nodeStyles}
        nodesDraggable
        onConnect={onConnect}
        onNodesSelect={onNodesSelect}
        onEdgeSelect={onEdgeSelect}
        onEdgeClick={onEdgeClick}
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
    expect(onNodesSelect).toHaveBeenLastCalledWith([nodes[0]]);
    expect(onEdgeSelect).toHaveBeenLastCalledWith(null);

    (
      props.onSelectionChange as (selection: {
        nodes: typeof flowNodes;
        edges: typeof flowEdges;
      }) => void
    )({ nodes: [], edges: [flowEdges[0]!] });
    expect(onNodesSelect).toHaveBeenLastCalledWith([]);
    expect(onEdgeSelect).toHaveBeenLastCalledWith(edges[0]);

    (props.onEdgeClick as (event: unknown, edge: (typeof flowEdges)[number]) => void)(
      undefined,
      flowEdges[0]!,
    );
    expect(onEdgeClick).toHaveBeenCalledWith(edges[0], { source: "pointer" });

    const edgeTarget = document.createElementNS("http://www.w3.org/2000/svg", "g");
    edgeTarget.setAttribute("data-graph-edge-id", "draft-review");
    const preventDefault = vi.fn();
    (props.onKeyDown as (event: unknown) => void)({ key: "Enter", target: edgeTarget, preventDefault });
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(onEdgeClick).toHaveBeenLastCalledWith(edges[0], { source: "keyboard" });
  });
});
