// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { createRef, type ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { GraphView, graphNodeStyle } from "./GraphView";

const reactFlowMock = vi.hoisted(() => ({
  lastProps: undefined as Record<string, unknown> | undefined,
  zeroBounds: false,
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
      const nodes = props.nodes as Array<{ id: string; position: { x: number; y: number }; style: { width: number; minHeight: number } }>;
      const bounds = (selected: typeof nodes) => {
        const left = Math.min(...selected.map((node) => node.position.x));
        const top = Math.min(...selected.map((node) => node.position.y));
        const right = Math.max(...selected.map((node) => node.position.x + node.style.width));
        const bottom = Math.max(...selected.map((node) => node.position.y + node.style.minHeight));
        return { x: left, y: top, width: right - left, height: bottom - top };
      };
      const intersects = (node: typeof nodes[number], rect: { x: number; y: number; width: number; height: number }) => node.position.x < rect.x + rect.width && node.position.x + node.style.width > rect.x && node.position.y < rect.y + rect.height && node.position.y + node.style.minHeight > rect.y;
      (props.onInit as ((instance: object) => void) | undefined)?.({
        getNode: (id: string) => nodes.find((node) => node.id === id),
        getEdge: (id: string) => (props.edges as Array<{ id: string }>).find((edge) => edge.id === id),
        getNodes: () => nodes,
        getNodesBounds: (selected: Array<string | typeof nodes[number]>) => {
          const resolved = selected.map((item) => typeof item === "string" ? nodes.find((node) => node.id === item)! : item);
          const value = bounds(resolved);
          return reactFlowMock.zeroBounds ? { ...value, width: 0, height: 0 } : value;
        },
        getIntersectingNodes: (rect: { x: number; y: number; width: number; height: number }) => nodes.filter((node) => intersects(node, rect)),
      });
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
  reactFlowMock.zeroBounds = false;
  dagreMock.layouts = 0;
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
  });

  test("exposes native rendered bounds and always finds a nonintersecting lane", () => {
    const geometry = createRef<import("./GraphView").GraphViewGeometry<"handler" | "blocker">>();
    render(<GraphView
      geometryRef={geometry}
      nodes={[
        { ...nodes[0], kind: "blocker", position: { x: 0, y: 0 } },
        { ...nodes[1], kind: "handler", position: { x: 1100, y: 0 } },
      ]}
      edges={[]}
      nodeStyles={{ ...nodeStyles, blocker: { ...nodeStyles.handler, width: 1000 } }}
    />);
    expect(geometry.current?.nodeBounds("draft")).toEqual({ x: 0, y: 0, width: 1000, height: 72 });
    const position = geometry.current!.firstFreePosition("handler", { x: 100, y: 0 });
    expect(geometry.current!.intersects({ ...position, width: 160, height: 72 })).toBe(false);
  });

  test("uses declared dimensions while native measurement is pending", () => {
    reactFlowMock.zeroBounds = true;
    const geometry = createRef<import("./GraphView").GraphViewGeometry<"handler" | "gate">>();
    render(<GraphView geometryRef={geometry} nodes={nodes} edges={edges} nodeStyles={nodeStyles} />);

    expect(geometry.current?.nodeBounds("draft")).toEqual(expect.objectContaining({
      width: nodeStyles.handler.width,
      height: nodeStyles.handler.height,
    }));
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
    const onNodeDragEnd = vi.fn();
    const onConnect = vi.fn();
    const onNodeSelect = vi.fn();
    const onEdgeSelect = vi.fn();
    const onEdgeClick = vi.fn();

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
