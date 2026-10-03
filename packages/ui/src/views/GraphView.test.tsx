// @vitest-environment happy-dom

import { act, cleanup, render, screen } from "@testing-library/react";
import { type ReactNode } from "react";
import type { Node } from "@xyflow/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { GraphView, graphNodeStyle } from "./GraphView";

const reactFlowMock = vi.hoisted(() => ({
  lastProps: undefined as Record<string, unknown> | undefined,
  initialized: true,
  handlesReady: true,
  unmeasured: new Set<string>(),
  fitView: vi.fn(),
  setViewport: vi.fn(),
  width: 1600,
  height: 600,
  viewportInitialized: true,
  miniMapProps: undefined as Record<string, unknown> | undefined,
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
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Background: () => React.createElement("div", { "data-testid": "background" }),
    Controls: () => React.createElement("div", { "data-testid": "controls" }),
    MiniMap: (props: Record<string, unknown>) => {
      reactFlowMock.miniMapProps = props;
      return React.createElement("div", { "data-testid": "mini-map" });
    },
    MarkerType: { ArrowClosed: "arrowclosed" },
    Position: { Bottom: "bottom", Top: "top", Right: "right", Left: "left" },
    ReactFlow: (props: Record<string, unknown> & { children?: ReactNode }) => {
      reactFlowMock.lastProps = props;
      return React.createElement(
        "div",
        { "data-testid": "react-flow" },
        props.children,
      );
    },
    ReactFlowProvider: ({ children }: { children: ReactNode }) => children,
    useNodesInitialized: () => reactFlowMock.initialized,
    useReactFlow: () => ({
      fitView: reactFlowMock.fitView,
      setViewport: reactFlowMock.setViewport,
      viewportInitialized: reactFlowMock.viewportInitialized,
      getNodes: () => reactFlowMock.lastProps?.nodes,
      getNodesBounds: (nodes: Node[]) => actual.getNodesBounds(nodes.map((node) => ({
        ...node, measured: { width: Number(node.style?.width), height: Number(node.style?.minHeight) },
      }))),
      getNode: (id: string) => (reactFlowMock.lastProps?.nodes as Array<{ id: string }>).find((node) => node.id === id),
      getEdge: (id: string) => (reactFlowMock.lastProps?.edges as Array<{ id: string }>).find((edge) => edge.id === id),
    }),
    useStore: (selector: (state: unknown) => unknown) => selector({ width: reactFlowMock.width, height: reactFlowMock.height, nodeLookup: { get: (id: string) => ({ internals: {
      handleBounds: reactFlowMock.handlesReady && !reactFlowMock.unmeasured.has(id) ? { source: [{ id: null }, { id: "done" }], target: [{ id: null }] } : undefined,
    } }) } }),
  };
});

afterEach(() => {
  cleanup();
  reactFlowMock.lastProps = undefined;
  dagreMock.layouts = 0;
  reactFlowMock.initialized = true;
  reactFlowMock.handlesReady = true;
  reactFlowMock.unmeasured.clear();
  reactFlowMock.fitView.mockClear();
  reactFlowMock.setViewport.mockClear();
  reactFlowMock.width = 1600;
  reactFlowMock.height = 600;
  reactFlowMock.viewportInitialized = true;
  reactFlowMock.miniMapProps = undefined;
});

test("a long graph initially fits only its cross-axis and anchors the source rather than array order", () => {
  const chain = Array.from({ length: 13 }, (_, index) => ({ id: `step-${index}`, kind: "handler", title: `Step ${index}`, position: { x: index * 280, y: 100 } }));
  const connections = chain.slice(1).map((node, index) => ({ id: `link-${index}`, source: chain[index]!.id, target: node.id, kind: "next" }));
  const props = { nodes: [...chain.slice(1), chain[0]!], edges: connections, nodeStyles, layout: { rankdir: "LR" as const } };
  const view = render(<GraphView {...props} initialView={{ minZoom: 0.65 }} />);
  const viewport = reactFlowMock.setViewport.mock.calls[0]![0];
  expect(viewport.zoom).toBe(1);
  expect(viewport.x + nodeStyles.handler.width / 2 * viewport.zoom).toBeCloseTo(800);
  expect(viewport.y + (100 + nodeStyles.handler.height / 2) * viewport.zoom).toBeCloseTo(300);
  expect(reactFlowMock.fitView).not.toHaveBeenCalled();
  expect(reactFlowMock.lastProps?.minZoom).toBe(0.05);
  view.rerender(<GraphView {...props} initialView={{ minZoom: 0.65 }} nodes={chain.map((node) => ({ ...node, title: "Edited" }))} />);
  expect(reactFlowMock.setViewport).toHaveBeenCalledTimes(1);
  view.rerender(<GraphView {...props} initialView={{ minZoom: 0.65 }} fitViewRequest={1} />);
  expect(reactFlowMock.fitView).toHaveBeenCalledWith(expect.objectContaining({ minZoom: 0.05 }));
});

test.each(["LR", "TB"] as const)("%s anchored fitting respects the readable floor on a large cross-axis", (rankdir) => {
  const graphNodes = [
    { id: "entry", kind: "handler", title: "Entry", position: { x: 50, y: 80 } },
    { id: "far", kind: "handler", title: "Far", position: { x: 3000, y: 3000 } },
  ];
  render(<GraphView nodes={graphNodes} edges={[]} nodeStyles={nodeStyles} layout={{ rankdir }} initialView={{ anchorNodeId: "far", minZoom: 0.7 }} />);
  const viewport = reactFlowMock.setViewport.mock.calls[0]![0];
  expect(viewport.zoom).toBe(0.7);
  const axis = rankdir === "LR" ? "x" : "y";
  const extent = rankdir === "LR" ? nodeStyles.handler.width : nodeStyles.handler.height;
  expect(viewport[axis] + (3000 + extent / 2) * viewport.zoom).toBeCloseTo(rankdir === "LR" ? 800 : 300);
  const crossAxis = rankdir === "LR" ? "y" : "x";
  const crossExtent = rankdir === "LR" ? nodeStyles.handler.height : nodeStyles.handler.width;
  expect(viewport[crossAxis] + (3000 + crossExtent / 2) * viewport.zoom).toBeCloseTo(rankdir === "LR" ? 300 : 800);
});

test("anchored view waits for projection, measurement, and a visible initialized viewport", () => {
  reactFlowMock.viewportInitialized = false;
  reactFlowMock.width = 0;
  const view = render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} initialView={{ ready: false }} />);
  reactFlowMock.viewportInitialized = true;
  view.rerender(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} initialView={{ ready: true }} />);
  expect(reactFlowMock.setViewport).not.toHaveBeenCalled();
  reactFlowMock.width = 1600;
  reactFlowMock.initialized = false;
  view.rerender(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} initialView={{ ready: true }} />);
  expect(reactFlowMock.setViewport).not.toHaveBeenCalled();
  reactFlowMock.initialized = true;
  view.rerender(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} initialView={{ ready: false }} />);
  expect(reactFlowMock.setViewport).not.toHaveBeenCalled();
  view.rerender(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} initialView={{ ready: true }} />);
  expect(reactFlowMock.setViewport).toHaveBeenCalledTimes(1);
});

test("MiniMap is opt-in and uses native pan and zoom navigation", () => {
  const view = render(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);
  expect(screen.queryByTestId("mini-map")).toBeNull();
  view.rerender(<GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} miniMap />);
  expect(screen.getByTestId("mini-map")).toBeTruthy();
  expect(reactFlowMock.miniMapProps).toMatchObject({ pannable: true, zoomable: true });
});

test("initial fit waits for native measurement and does not depend on edges", () => {
  reactFlowMock.initialized = false;
  reactFlowMock.handlesReady = false;
  const graph = () => <GraphView nodes={nodes} edges={edges} nodeStyles={nodeStyles} />;
  const view = render(graph());
  expect(reactFlowMock.lastProps?.edges).toEqual([]);
  expect(reactFlowMock.fitView).not.toHaveBeenCalled();
  reactFlowMock.initialized = true;
  view.rerender(graph());
  expect(reactFlowMock.fitView).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ minZoom: 0.05 }));
  reactFlowMock.handlesReady = true;
  view.rerender(graph());
  expect(reactFlowMock.lastProps?.edges).toHaveLength(1);
  expect(reactFlowMock.fitView).toHaveBeenCalledOnce();
});

test("topology, outcome, and layout updates preserve the viewport until a fit is requested", () => {
  const props = { nodes, edges, nodeStyles };
  const view = render(<GraphView {...props} />);
  view.rerender(<GraphView {...props} nodes={[...nodes, { id: "extra", kind: "handler", title: "Extra" }]} edges={[]} />);
  reactFlowMock.initialized = false;
  view.rerender(<GraphView {...props} nodes={nodes.map((node) => ({ ...node, ports: [{ id: "done" }] }))} />);
  reactFlowMock.initialized = true;
  view.rerender(<GraphView {...props} layout={{ rankdir: "LR" }} fitViewOptions={{ padding: 0.3 }} />);
  expect(reactFlowMock.fitView).toHaveBeenCalledOnce();
  view.rerender(<GraphView {...props} fitViewRequest={1} />);
  expect(reactFlowMock.fitView).toHaveBeenCalledTimes(2);
  expect(currentProps().onInit).toBeUndefined();
});

test("valid edges mount together once measured; impossible handles are excluded and warned once", () => {
  const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const extra = { id: "extra", kind: "handler", title: "Extra", ports: [{ id: "done" }] };
  reactFlowMock.initialized = false;
  reactFlowMock.unmeasured.add("extra");
  const allEdges = [...edges,
    { id: "waiting", source: "extra", sourceHandle: "done", target: "review", kind: "success" },
    { id: "impossible", source: "extra", sourceHandle: "retired", target: "review", kind: "success" },
  ];
  const graph = () => <GraphView nodes={[...nodes, extra]} edges={allEdges} nodeStyles={nodeStyles} />;
  const view = render(graph());
  // Mounting edges one by one re-filters the set as bounds land and loops
  // React Flow's edge-label measurement, so none mount before all are ready.
  expect((currentProps().edges as Array<{ id: string }>).map((edge) => edge.id)).toEqual([]);
  expect(warn).toHaveBeenCalledExactlyOnceWith(expect.stringContaining("impossible"));
  reactFlowMock.unmeasured.clear();
  reactFlowMock.initialized = true;
  view.rerender(graph());
  expect((currentProps().edges as Array<{ id: string }>).map((edge) => edge.id)).toEqual(["draft-review", "waiting"]);
  expect(reactFlowMock.fitView).toHaveBeenCalledOnce();
  expect(warn).toHaveBeenCalledOnce();
  warn.mockRestore();
});

test("a long horizontal workflow fits below React Flow's default zoom floor", async () => {
  const chain = Array.from({ length: 13 }, (_, index) => ({ id: String(index), kind: "handler", title: `Step ${index}` }));
  const connections = chain.slice(1).map((node, index) => ({ id: node.id, source: String(index), target: node.id, kind: "success" }));
  render(<GraphView nodes={chain} edges={connections} nodeStyles={nodeStyles} layout={{ rankdir: "LR" }} />);
  const rendered = currentProps().nodes as Array<{ position: { x: number; y: number }; sourcePosition: string; targetPosition: string }>;
  expect(rendered.every((node) => node.sourcePosition === "right" && node.targetPosition === "left")).toBe(true);
  expect(rendered[12]!.position.x).toBeGreaterThan(rendered[0]!.position.x + 2000);
  expect(new Set(rendered.map((node) => node.position.y)).size).toBe(1);
  expect(currentProps().minZoom).toBe(0.05);
  const { getNodesBounds, getViewportForBounds } = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  const bounds = getNodesBounds(rendered.map((node, index) => ({ position: node.position, id: String(index), data: {}, width: 160, height: 72 })));
  const viewport = getViewportForBounds(bounds, 1600, 800, currentProps().minZoom as number, 2, 0.18);
  expect(viewport.zoom).toBeLessThan(0.5);
  expect(bounds.x * viewport.zoom + viewport.x).toBeGreaterThanOrEqual(0);
  expect((bounds.x + bounds.width) * viewport.zoom + viewport.x).toBeLessThanOrEqual(1600);
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
      edges={edges.map((edge) => ({ ...edge, kind: "taken", label: "renamed outcome" }))}
      nodeStyles={{ ...nodeStyles }}
      edgeStyles={{ taken: { stroke: "var(--brand)", strokeWidth: 2 } }}
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
    expect(currentProps().ariaLabelConfig).toMatchObject({
      "node.a11yDescription.default": "Press Enter or Space to select a node, or Escape to cancel.",
      "minimap.ariaLabel": "Graph overview",
    });
    expect(screen.queryByRole("status")).toBeNull();
    const label = render(flowNode.data.label);
    expect(label.getByText("Operation")).toBeTruthy();
    expect(label.getByText("handler").tagName).toBe("CODE");
  });

  test("equivalent live status and port values preserve cached nodes and layout", () => {
    const node = { ...nodes[0]!, ports: [{ id: "done", label: "Done" }], detail: "1 Succeeded" };
    const view = render(<GraphView nodes={[node]} edges={[]} nodeStyles={nodeStyles}
      status={{ [node.id]: { label: "1/1", tone: "success" } }} />);
    const before = (currentProps().nodes as Node[])[0];
    const layouts = dagreMock.layouts;
    view.rerender(<GraphView nodes={[{ ...node, ports: [{ id: "done", label: "Done" }] }]}
      edges={[]} nodeStyles={nodeStyles} status={{ [node.id]: { label: "1/1", tone: "success" } }} />);
    expect((currentProps().nodes as Node[])[0]).toBe(before);
    expect(dagreMock.layouts).toBe(layouts);
    view.rerender(<GraphView nodes={[node]} edges={[]} nodeStyles={nodeStyles}
      status={{ [node.id]: { label: "2/2", tone: "warning" } }} />);
    expect((currentProps().nodes as Node[])[0]).not.toBe(before);
    expect(dagreMock.layouts).toBe(layouts);
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
