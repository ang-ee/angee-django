// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { GraphView } from "./GraphView";

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
  test("renders controlled node selection with the shared selected recipe", async () => {
    const graph = (selected: boolean) => <GraphView
      className="h-[360px] w-[520px]"
      nodes={[{ id: "a", kind: "step", title: "A", selected }]}
      edges={[]}
      nodeStyles={{ step: { width: 160, height: 72, borderColor: "var(--border-subtle)" } }}
    />;
    const view = render(graph(true));
    const node = await screen.findByTestId("rf__node-a");
    expect(node.className).toContain("selected");
    expect(node.style.borderColor).toBe("var(--brand)");
    expect(node.style.background).toBe("var(--brand-soft)");
    expect(node.style.borderWidth).toBe("2px");

    view.rerender(graph(false));
    await waitFor(() => expect(node.className).not.toContain("selected"));
    expect(node.style.borderColor).toBe("var(--border-subtle)");
    expect(node.style.background).toBe("var(--surface-sheet)");
    expect(node.style.borderWidth).toBe("1px");
  });

  test("forwards a programmatic focus target for pane navigation", async () => {
    const surface = React.createRef<HTMLDivElement>();
    render(
      <GraphView
        surfaceRef={surface}
        ariaLabel="Workflow editor"
        className="h-[360px] w-[520px]"
        nodes={[]}
        edges={[]}
        nodeStyles={{}}
      />,
    );

    surface.current?.focus();
    expect(document.activeElement).toBe(surface.current);
    expect(screen.getByRole("region", { name: "Workflow editor" })).toBe(surface.current);
  });

  test("selects a node through the real xyflow canvas", async () => {
    const onNodeSelect = vi.fn();

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
        onNodeSelect={onNodeSelect}
      />,
    );

    expect(screen.getByLabelText("Draft validation, entry step")).toBeTruthy();

    fireEvent.click(screen.getByText("Draft"));

    await waitFor(() => {
      expect(onNodeSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: "draft" }),
      );
    });
  });

  test("reports whether native graph activation came from pointer or keyboard", async () => {
    const onNodeClick = vi.fn();
    const onNodeSelect = vi.fn();
    render(
      <GraphView
        className="h-[360px] w-[520px]"
        nodes={[{ id: "draft", kind: "step", title: "Draft" }]}
        edges={[]}
        nodeStyles={{ step: { width: 160, height: 72, borderColor: "var(--border-subtle)" } }}
        onNodeClick={onNodeClick}
        onNodeSelect={onNodeSelect}
      />,
    );
    const node = await screen.findByTestId("rf__node-draft");

    fireEvent.click(node, { detail: 1 });
    await waitFor(() => expect(onNodeSelect).toHaveBeenCalledWith(expect.objectContaining({ id: "draft" })));
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
