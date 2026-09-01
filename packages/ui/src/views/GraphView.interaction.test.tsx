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
  test("selects a node through the real xyflow canvas", async () => {
    const onNodeSelect = vi.fn();

    render(
      <GraphView
        className="h-[360px] w-[520px]"
        nodes={[
          { id: "draft", kind: "handler", title: "Draft", code: "handler" },
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

    fireEvent.click(screen.getByText("Draft"));

    await waitFor(() => {
      expect(onNodeSelect).toHaveBeenCalledWith(
        expect.objectContaining({ id: "draft" }),
      );
    });
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
