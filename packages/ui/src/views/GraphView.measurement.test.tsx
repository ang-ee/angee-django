// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { ReactFlowInstance, ReactFlowProps } from "@xyflow/react";
import { createUiTestProviders } from "../testing";
import { GraphView } from "./GraphView";
import { GraphEditor } from "./GraphEditor";

const capture = vi.hoisted(() => ({ props: null as ReactFlowProps | null, instance: null as ReactFlowInstance | null, width: 0 }));
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...actual,
    ReactFlow: (props: ReactFlowProps) => {
      capture.props = props;
      capture.instance = actual.useReactFlow();
      capture.width = actual.useStore((state) => state.width);
      return <actual.ReactFlow {...props} />;
    },
  };
});
const { Provider } = createUiTestProviders();
afterEach(cleanup);

test("native measured outcome lists leave room for every branch", async () => {
  const nodes = ["entry", "context", "classification", "bank"].map((id) => ({ id, kind: "node", title: id,
    ports: [{ id: "done", label: "Done" }] }));
  const edges = nodes.slice(1).map((node) => ({ id: node.id, source: "entry", sourceHandle: "done", target: node.id, kind: "next" }));
  render(<GraphView nodes={nodes} edges={edges} layout={{ rankdir: "LR" }}
    nodeStyles={{ node: { width: 244, height: 100, borderColor: "gray" } }} />, { wrapper: Provider });
  act(() => capture.props?.onNodesChange?.(nodes.map((node) => ({ type: "dimensions" as const,
    id: node.id, dimensions: { width: 244, height: node.id === "entry" ? 110 : 290 } }))));
  await waitFor(() => {
    const branches = capture.instance!.getNodes().filter((node) => node.id !== "entry").sort((a, b) => a.position.y - b.position.y);
    expect(branches).toHaveLength(3);
    for (let index = 1; index < branches.length; index += 1) {
      expect(branches[index]!.position.y).toBeGreaterThanOrEqual(branches[index - 1]!.position.y + 290 + 34);
    }
  });
});

test("native measured anchored viewport is readable and the Fit control still fits a long graph", async () => {
  const nodes = Array.from({ length: 13 }, (_, index) => ({ id: `node-${index}`, kind: "node", title: `Item ${index}` }));
  const edges = nodes.slice(1).map((node, index) => ({ id: `edge-${index}`, source: nodes[index]!.id, target: node.id, kind: "next" }));
  render(<GraphView nodes={nodes} edges={edges} nodeStyles={{ node: { width: 160, height: 80, borderColor: "gray" } }}
    layout={{ rankdir: "LR" }} initialView={{ minZoom: 0.7 }} miniMap />, { wrapper: Provider });
  act(() => capture.props?.onNodesChange?.(nodes.map((node) => ({
    type: "dimensions" as const, id: node.id, dimensions: { width: 160, height: 80 },
  }))));
  await waitFor(() => expect(capture.instance!.getViewport().zoom).toBe(1));
  const anchor = capture.instance!.getNodesBounds(["node-0"]);
  const viewport = capture.instance!.getViewport();
  expect(viewport.x + (anchor.x + anchor.width / 2) * viewport.zoom).toBeCloseTo(capture.width / 2);
  expect(document.querySelector(".react-flow__minimap")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /fit view/i }));
  await waitFor(() => expect(capture.instance!.getViewport().zoom).toBeLessThan(0.7));
});

test("native adoption retains measurements and unchanged node identities after selection", () => {
  // Use the native canvas and the DOM environment's ResizeObserver. Feed its dimensions
  // protocol explicitly because this environment does not perform browser layout.
  const nodes = [{ id: "a", kind: "node", title: "Alpha" }, { id: "b", kind: "node", title: "Beta" }];
  const styles = { node: { width: 160, height: 80, borderColor: "gray" } };
  const graph = (selected: string) => <GraphView
    nodes={nodes.map((node) => ({ ...node, selected: node.id === selected }))}
    edges={[]} nodeStyles={styles} onNodesSelect={vi.fn()} />;
  const view = render(graph(""), { wrapper: Provider });
  act(() => capture.props?.onNodesChange?.([
    { type: "dimensions", id: "a", dimensions: { width: 160, height: 80 } },
    { type: "dimensions", id: "b", dimensions: { width: 160, height: 80 } },
  ]));
  const before = capture.props!.nodes!;
  view.rerender(graph("a"));
  const after = capture.props!.nodes!;
  expect(after[0]?.measured).toEqual({ width: 160, height: 80 });
  expect(after[1]).toBe(before[1]);
  expect(screen.getByTestId("rf__node-a").style.visibility).not.toBe("hidden");
  act(() => capture.props?.onNodesChange?.([{ type: "position", id: "a", position: { x: 200, y: 300 }, dragging: true }]));
  expect(capture.props!.nodes![1]).toBe(after[1]);
  expect(capture.props!.nodes![0]?.measured).toEqual({ width: 160, height: 80 });
});

test("a native drag sequence reports one settled editor layout containing every node", () => {
  const changed = vi.fn();
  render(<GraphEditor
    nodes={[{ id: "a", kind: "node", title: "Alpha", ports: [] }, { id: "b", kind: "node", title: "Beta", ports: [] }]}
    links={[]} layout={{}} canLink={() => true} onLink={vi.fn()} onUnlink={vi.fn()}
    onDelete={vi.fn()} onLayoutChange={changed} onAddFromPort={vi.fn()} onInsertOnLink={vi.fn()}
  />, { wrapper: Provider });
  const second = capture.props!.nodes!.find((node) => node.id === "b")!.position;
  for (const x of [40, 50, 60]) {
    act(() => capture.props?.onNodesChange?.([{ type: "position", id: "a", position: { x, y: 70 }, dragging: true }]));
  }
  expect(changed).not.toHaveBeenCalled();
  act(() => capture.props?.onNodesChange?.([{ type: "position", id: "a", position: { x: 60, y: 70 }, dragging: false }]));
  expect(changed).toHaveBeenCalledExactlyOnceWith({ a: { x: 60, y: 70 }, b: second });
});
