// @vitest-environment happy-dom
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { createUiTestProviders } from "../testing";
import { GraphEditor, type GraphEditorProps } from "./GraphEditor";
import type { GraphViewProps } from "./GraphView";

const flow = vi.hoisted(() => ({ props: null as GraphViewProps | null }));
vi.mock("./GraphView", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./GraphView")>();
  return { ...actual, GraphView: (props: GraphViewProps) => { flow.props = props; return <div data-testid="canvas"><div data-graph-node-id="a" tabIndex={0}>Canvas node</div></div>; } };
});
const { Provider } = createUiTestProviders();
afterEach(cleanup);
const nodes = [
  { id: "a", kind: "node", title: "Alpha", ariaLabel: "Alpha", ports: [{ id: "one", label: "First" }, { id: "two", label: "Second" }] },
  { id: "b", kind: "node", title: "Beta", ariaLabel: "Beta", ports: [{ id: "next", label: "Next" }] },
  { id: "c", kind: "node", title: "Gamma", ariaLabel: "Gamma", ports: [] },
];
const link = { from: "a", port: "one", to: "b" };
function setup(overrides: Partial<GraphEditorProps> = {}) {
  const props: GraphEditorProps = { nodes, links: [link], layout: { a: { x: 20, y: 20 } }, canLink: vi.fn(() => true), onLink: vi.fn(), onUnlink: vi.fn(), onDelete: vi.fn(), onLayoutChange: vi.fn(), onInsertOnLink: vi.fn(), onAddFromPort: vi.fn(), ...overrides };
  return { props, view: render(<GraphEditor {...props} />, { wrapper: Provider }) };
}
function canvas(): GraphViewProps {
  if (!flow.props) throw new Error("Canvas has not rendered");
  return flow.props;
}

describe("GraphEditor", () => {
  test("horizontal automatic layout and explicit auto-layout fit use the same options", () => {
    const { props } = setup({ layout: {}, layoutOptions: { rankdir: "LR" }, initialView: { anchorNodeId: "a", minZoom: 0.7 }, miniMap: true });
    expect(canvas().layout).toEqual({ rankdir: "LR" });
    expect(canvas().initialView).toEqual({ anchorNodeId: "a", minZoom: 0.7 });
    expect(canvas().miniMap).toBe(true);
    const [alpha, beta] = canvas().nodes;
    expect(beta!.position!.x).toBeGreaterThan(alpha!.position!.x);
    expect(beta!.position!.y).toBe(alpha!.position!.y);
    expect(canvas().fitViewRequest).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Auto-layout" }));
    expect(props.onLayoutChange).toHaveBeenCalledOnce();
    expect(canvas().fitViewRequest).toBe(1);
  });
  test("the flow gets priority height and automatic display layout does not author a change", () => {
    const resolved = vi.fn();
    const { props } = setup({ layout: {}, onLayoutResolved: resolved });
    expect(canvas().className).toContain("flex-[3]");
    expect(canvas().className).toContain("min-h-0");
    expect(screen.getByRole("heading", { name: "Connections" }).parentElement?.className).toContain("max-h-36");
    expect(screen.getByRole("heading", { name: "Connections" }).parentElement?.className).toContain("overflow-auto");
    expect(resolved).toHaveBeenCalledExactlyOnceWith(Object.fromEntries(canvas().nodes.map((node) => [node.id, node.position])));
    expect(props.onLayoutChange).not.toHaveBeenCalled();
    expect(new Set(canvas().nodes.map((node) => node.position?.x)).size).toBeGreaterThan(1);
  });
  test("rejects duplicate links through the canvas and accessible connection list", async () => {
    const { props } = setup();
    const duplicate = { source: "a", sourceHandle: "one", target: "b" };
    expect(canvas().isValidConnection?.(duplicate)).toBe(false);
    canvas().onConnect?.(duplicate);
    canvas().onReconnect?.(canvas().edges[0]!, duplicate);
    expect(props.onLink).not.toHaveBeenCalled();
    expect(props.onUnlink).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Connect First from Alpha" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect" });
    fireEvent.keyDown(within(dialog).getByRole("combobox", { name: "Target node" }), { key: "ArrowDown" });
    expect((await screen.findByRole("option", { name: "Beta" })).getAttribute("aria-disabled")).toBe("true");
  });

  test("read-only permits selection but blocks all editing and uses missing style defaults", () => {
    const action = vi.fn();
    const { props } = setup({ readOnly: true, nodeStyles: {}, nodeActions: () => [{ id: "duplicate", label: "Duplicate", onSelect: action }] });
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    expect(canvas().nodesDraggable).toBe(false);
    expect(canvas().onConnect).toBeUndefined();
    expect(canvas().onReconnect).toBeUndefined();
    for (const name of ["Auto-layout", "Delete selection", "Duplicate: Alpha", "Connect First from Alpha", "Add from First on Alpha", "Delete Alpha", "Reconnect Alpha, First, to Beta", "Insert on Alpha, First, to Beta", "Delete link from Alpha, First, to Beta"]) {
      const button = screen.getByRole("button", { name });
      expect(button.hasAttribute("disabled")).toBe(true);
      fireEvent.click(button);
    }
    fireEvent.keyDown(screen.getByText("Canvas node"), { key: "Delete" });
    canvas().onNodesPositionChange?.({ a: { x: 1, y: 2 } });
    expect(props.onLayoutChange).not.toHaveBeenCalled();
    expect(props.onDelete).not.toHaveBeenCalled();
    expect(props.onLink).not.toHaveBeenCalled();
    expect(props.onUnlink).not.toHaveBeenCalled();
    expect(action).not.toHaveBeenCalled();
    expect(canvas().nodeStyles.node).toEqual(expect.objectContaining({ width: 188, height: 100 }));
  });

  test("suggested additions preserve all resolved positions for unpinned nodes", () => {
    const { props, view } = setup({ layout: {}, nodeStyles: {} });
    const initial = Object.fromEntries(canvas().nodes.map((node) => [node.id, node.position!]));
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Add from Second on Alpha" }));
    expect(props.onLayoutChange).toHaveBeenCalledExactlyOnceWith(initial);
    const suggested = vi.mocked(props.onAddFromPort).mock.calls[0]![2];
    expect(suggested).toEqual(expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }));
    view.rerender(<GraphEditor {...props} nodes={[...nodes, { id: "d", kind: "new", title: "Delta", ports: [] }]} layout={{ ...initial, d: suggested }} />);
    expect(Object.fromEntries(canvas().nodes.slice(0, 3).map((node) => [node.id, node.position]))).toEqual(initial);
  });

  test("a dangling link remains removable but cannot request insertion beside a missing source", () => {
    const { props } = setup({ nodes: nodes.slice(1) });
    const insert = screen.getByRole("button", { name: "Insert on a, one, to Beta" });
    expect(insert.hasAttribute("disabled")).toBe(true);
    fireEvent.click(insert);
    expect(props.onInsertOnLink).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete link from a, one, to Beta" }));
    expect(props.onUnlink).toHaveBeenCalledWith(link);
  });

  test("local selection does not return when removed nodes and links are reintroduced", () => {
    const { props, view } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    act(() => canvas().onEdgeSelect?.(canvas().edges[0]!));
    view.rerender(<GraphEditor {...props} nodes={nodes.slice(1)} links={[]} />);
    view.rerender(<GraphEditor {...props} />);
    expect(canvas().nodes.every((node) => !node.selected)).toBe(true);
    expect(canvas().edges.every((edge) => !edge.selected)).toBe(true);
    expect(screen.getByRole("button", { name: "Delete selection" }).hasAttribute("disabled")).toBe(true);
  });

  test("delegates pointer connections and reconnects through the supplied rule, including self links", () => {
    const canLink = vi.fn((_from: string, port: string) => port !== "two");
    const { props } = setup({ canLink });
    const allowed = { source: "a", sourceHandle: "one", target: "a" };
    const blocked = { source: "a", sourceHandle: "two", target: "c" };
    canvas().onConnect?.(allowed);
    canvas().onConnect?.(blocked);
    expect(props.onLink).toHaveBeenCalledExactlyOnceWith({ from: "a", port: "one", to: "a" });
    canvas().onReconnect?.(canvas().edges[0]!, blocked);
    expect(props.onUnlink).not.toHaveBeenCalled();
    canvas().onReconnect?.(canvas().edges[0]!, { source: "b", sourceHandle: "next", target: "c" });
    expect(props.onUnlink).toHaveBeenCalledExactlyOnceWith(link);
    expect(props.onLink).toHaveBeenLastCalledWith({ from: "b", port: "next", to: "c" });
    expect(canLink).toHaveBeenCalledWith("a", "one", "a");
  });

  test("keeps data controlled and reports layout without deleting local records", () => {
    const { props, view } = setup();
    canvas().onNodesPositionChange?.({ a: { x: 70, y: 80 }, b: { x: 100, y: 110 } });
    expect(props.onLayoutChange).toHaveBeenCalledWith(expect.objectContaining({ a: { x: 70, y: 80 }, b: { x: 100, y: 110 }, c: expect.any(Object) }));
    expect(canvas().nodes[0]?.position).toEqual({ x: 20, y: 20 });
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    fireEvent.keyDown(screen.getByText("Canvas node"), { key: "Delete" });
    expect(props.onDelete).toHaveBeenCalledWith(["a"]);
    expect(canvas().nodes).toHaveLength(3);
    view.rerender(<GraphEditor {...props} nodes={nodes.slice(1)} layout={{ b: { x: 300, y: 200 } }} />);
    expect(canvas().nodes.map((node) => node.id)).toEqual(["b", "c"]);
    expect(canvas().nodes[0]?.position).toEqual({ x: 300, y: 200 });
  });

  test("dispatches insertion, adding, duplicate actions, unlinking, and multi-selection deletion", () => {
    const duplicate = vi.fn();
    const { props } = setup({ nodeActions: () => [{ id: "duplicate", label: "Duplicate", onSelect: duplicate }] });
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Duplicate: Alpha" }));
    expect(duplicate).toHaveBeenCalledWith(nodes[0]);
    fireEvent.click(screen.getByRole("button", { name: "Add from Second on Alpha" }));
    expect(props.onAddFromPort).toHaveBeenCalledWith("a", "two", expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }));
    fireEvent.click(screen.getByRole("button", { name: "Insert on Alpha, First, to Beta" }));
    expect(props.onInsertOnLink).toHaveBeenCalledWith(link, expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }));
    fireEvent.click(within(screen.getByRole("list")).getByRole("button", { name: "Delete link from Alpha, First, to Beta" }));
    expect(props.onUnlink).toHaveBeenCalledWith(link);
    act(() => canvas().onNodesSelect?.(canvas().nodes.slice(0, 2)));
    fireEvent.click(screen.getByRole("button", { name: "Delete selection" }));
    expect(props.onDelete).toHaveBeenLastCalledWith(["a", "b"]);
    fireEvent.click(screen.getByRole("button", { name: "Auto-layout" }));
    expect(props.onLayoutChange).toHaveBeenCalledWith(expect.objectContaining({ a: expect.objectContaining({ x: expect.any(Number), y: expect.any(Number) }), b: expect.any(Object), c: expect.any(Object) }));
  });

  test("connects through labelled keyboard controls and disables disallowed targets", async () => {
    const { props } = setup({ canLink: (_from, _port, to) => to === "c" });
    fireEvent.click(screen.getByRole("button", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("button", { name: "Connect Second from Alpha" }));
    const dialog = await screen.findByRole("dialog", { name: "Connect" });
    const target = within(dialog).getByRole("combobox", { name: "Target node" });
    fireEvent.keyDown(target, { key: "ArrowDown" });
    const gamma = await screen.findByRole("option", { name: "Gamma" });
    expect(screen.getByRole("option", { name: "Beta" }).getAttribute("aria-disabled")).toBe("true");
    fireEvent.keyDown(gamma, { key: "Enter" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Connect" }));
    expect(props.onLink).toHaveBeenCalledExactlyOnceWith({ from: "a", port: "two", to: "c" });
  });

  test("reconnects using the connection list and rechecks a changed rule before submit", async () => {
    const { props, view } = setup();
    fireEvent.click(screen.getByRole("button", { name: "Reconnect Alpha, First, to Beta" }));
    const dialog = await screen.findByRole("dialog", { name: "Reconnect" });
    fireEvent.keyDown(within(dialog).getByRole("combobox", { name: "Target node" }), { key: "ArrowDown" });
    fireEvent.keyDown(await screen.findByRole("option", { name: "Gamma" }), { key: "Enter" });
    view.rerender(<GraphEditor {...props} canLink={() => false} />);
    expect(within(dialog).getByRole("button", { name: "Reconnect" }).hasAttribute("disabled")).toBe(true);
    expect(props.onUnlink).not.toHaveBeenCalled();
    view.rerender(<GraphEditor {...props} />);
    fireEvent.click(within(dialog).getByRole("button", { name: "Reconnect" }));
    expect(props.onUnlink).toHaveBeenCalledExactlyOnceWith(link);
    expect(props.onLink).toHaveBeenCalledExactlyOnceWith({ ...link, to: "c" });
  });
});
