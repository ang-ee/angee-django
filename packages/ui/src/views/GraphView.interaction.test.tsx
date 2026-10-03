// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { GraphView } from "./GraphView";
import { createUiTestProviders } from "../testing";

const { Provider } = createUiTestProviders();

beforeAll(() => {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", {
    configurable: true,
    value: ResizeObserverStub,
  });
});

afterEach(() => {
  cleanup();
});

describe("GraphView interactions", () => {
  test("controlled native selection supports adding, toggling, and clearing multiple nodes", async () => {
    const changed = vi.fn();
    function ControlledSelection() {
      const [selected, setSelected] = React.useState<readonly string[]>([]);
      return <GraphView className="h-[360px] w-[520px]"
        nodes={["a", "b"].map((id) => ({ id, kind: "node", title: id, selected: selected.includes(id) }))}
        edges={[]} nodeStyles={{ node: { width: 160, height: 72, borderColor: "gray" } }}
        onNodesSelect={(nodes) => { const ids = nodes.map((node) => node.id); changed(ids); setSelected(ids); }} />;
    }
    const view = render(<ControlledSelection />, { wrapper: Provider });
    const first = await screen.findByTestId("rf__node-a");
    const second = screen.getByTestId("rf__node-b");
    fireEvent.click(first);
    await waitFor(() => expect(first.className).toContain("selected"));
    const modifier = navigator.userAgent.includes("Mac") ? "Meta" : "Control";
    fireEvent.keyDown(window, { key: modifier });
    fireEvent.click(second);
    await waitFor(() => expect(changed).toHaveBeenLastCalledWith(["a", "b"]));
    expect(first.className).toContain("selected");
    expect(second.className).toContain("selected");
    fireEvent.click(first);
    await waitFor(() => expect(changed).toHaveBeenLastCalledWith(["b"]));
    fireEvent.keyUp(window, { key: modifier });
    expect(first.className).not.toContain("selected");
    fireEvent.click(view.container.querySelector(".react-flow__pane")!);
    await waitFor(() => expect(changed).toHaveBeenLastCalledWith([]));
    expect(second.className).not.toContain("selected");
  });

  test("selection adds a ring independently of the highlighted execution state", async () => {
    const graph = (selected: boolean, highlighted = false) => <GraphView
      className="h-[360px] w-[520px]"
      nodes={[{ id: "a", kind: "step", title: "A", selected, highlighted }]}
      edges={[]}
      nodeStyles={{ step: { width: 160, height: 72, borderColor: "var(--border-subtle)" } }}
    />;
    const view = render(graph(true));
    const node = await screen.findByTestId("rf__node-a");
    expect(node.className).toContain("selected");
    expect(node.style.borderColor).toBe("var(--border-subtle)");
    expect(node.style.background).toBe("var(--surface-sheet)");
    expect(node.style.borderWidth).toBe("1px");
    expect(node.style.boxShadow).toBe("0 0 0 3px var(--surface-sheet), 0 0 0 5px var(--brand)");
    expect(node.style.outline).toBe("");

    view.rerender(graph(true, true));
    await waitFor(() => expect(node.style.borderWidth).toBe("2px"));
    expect(node.style.borderColor).toBe("var(--brand)");
    expect(node.style.background).toBe("var(--brand-soft)");
    expect(node.style.boxShadow).toBe("0 0 0 3px var(--surface-sheet), 0 0 0 5px var(--brand)");

    view.rerender(graph(false, true));
    await waitFor(() => expect(node.className).not.toContain("selected"));
    expect(node.style.borderWidth).toBe("2px");
    expect(node.style.boxShadow).toBe("");

    view.rerender(graph(false));
    await waitFor(() => expect(node.className).not.toContain("selected"));
    expect(node.style.borderColor).toBe("var(--border-subtle)");
    expect(node.style.background).toBe("var(--surface-sheet)");
    expect(node.style.borderWidth).toBe("1px");
  });

  test("selects a node through the real xyflow canvas", async () => {
    const onNodesSelect = vi.fn();

    render(
      <GraphView
        className="h-[360px] w-[520px]"
        nodes={[
          { id: "draft", kind: "handler", title: "Draft", code: "handler", ariaLabel: "Draft validation, entry step" },
          { id: "review", kind: "gate", title: "Review", code: "gate" },
        ]}
        edges={[
          {
            id: "draft-review",
            source: "draft",
            target: "review",
            kind: "default",
          },
        ]}
        nodeStyles={{
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
        }}
        onNodesSelect={onNodesSelect}
      />,
    );

    expect(screen.getByLabelText("Draft validation, entry step")).toBeTruthy();

    fireEvent.click(screen.getByText("Draft"));

    await waitFor(() => {
      expect(onNodesSelect).toHaveBeenCalledWith(
        [expect.objectContaining({ id: "draft" })],
      );
    });
  });

  test("reports whether native graph activation came from pointer or keyboard", async () => {
    const onNodeClick = vi.fn();
    const onNodesSelect = vi.fn();
    render(
      <GraphView
        className="h-[360px] w-[520px]"
        nodes={[{ id: "draft", kind: "step", title: "Draft" }]}
        edges={[]}
        nodeStyles={{ step: { width: 160, height: 72, borderColor: "var(--border-subtle)" } }}
        onNodeClick={onNodeClick}
        onNodesSelect={onNodesSelect}
      />,
    );
    const node = await screen.findByTestId("rf__node-draft");

    fireEvent.click(node, { detail: 1 });
    await waitFor(() => expect(onNodesSelect).toHaveBeenCalledWith([expect.objectContaining({ id: "draft" })]));
    fireEvent.keyDown(node, { key: "Enter" });
    await waitFor(() => expect(onNodeClick).toHaveBeenCalledTimes(2));
    fireEvent.keyDown(node, { key: " " });
    await waitFor(() => expect(onNodeClick).toHaveBeenCalledTimes(3));
    fireEvent.keyDown(screen.getByTitle("Fit View"), { key: "Enter" });
    expect(onNodeClick).toHaveBeenCalledTimes(3);

    expect(onNodeClick).toHaveBeenNthCalledWith(1, expect.objectContaining({ id: "draft" }), { source: "pointer" });
    expect(onNodeClick).toHaveBeenNthCalledWith(2, expect.objectContaining({ id: "draft" }), { source: "keyboard" });
    expect(onNodeClick).toHaveBeenNthCalledWith(3, expect.objectContaining({ id: "draft" }), { source: "keyboard" });
  });

  test("survives a consumer that sets state from selection with inline layout", async () => {
    // Regression: the nexus graph page passes `layout` as an inline literal and
    // stores every selection emission as fresh state. Re-emitting an unchanged
    // selection after each store resync looped until React threw "Maximum
    // update depth exceeded" in xyflow's StoreUpdater.
    const selectionEmissions = vi.fn();
    // Stable graph data, as GraphPage memoizes it; the inline `layout` literal
    // and per-emission fresh state are the pathological parts.
    const nodes = [
      { id: "draft", kind: "handler", title: "Draft", code: "handler" },
      { id: "review", kind: "gate", title: "Review", code: "gate" },
    ] as const;
    const edges = [
      { id: "draft-review", source: "draft", target: "review", kind: "default" },
    ] as const;
    const nodeStyles = {
      handler: { width: 160, height: 72, borderColor: "var(--border-subtle)" },
      gate: { width: 160, height: 72, borderColor: "var(--border-subtle)" },
    } as const;

    function PathologicalConsumer(): React.ReactElement {
      const [, setSelectedIds] = React.useState<readonly string[]>([]);
      return (
        <GraphView
          className="h-[360px] w-[520px]"
          nodes={nodes}
          edges={edges}
          nodeStyles={nodeStyles}
          layout={{ rankdir: "LR" }}
          onNodesSelect={(selected) => {
            selectionEmissions(selected.map((node) => node.id));
            setSelectedIds(selected.map((node) => node.id));
          }}
        />
      );
    }

    render(<PathologicalConsumer />);

    fireEvent.click(screen.getByText("Draft"));
    await waitFor(() => {
      expect(selectionEmissions).toHaveBeenCalledWith(["draft"]);
    });
    // The initial empty selection and the click each emit exactly once —
    // never once per store resync.
    expect(selectionEmissions.mock.calls).toEqual([[[]], [["draft"]]]);
  });
});
