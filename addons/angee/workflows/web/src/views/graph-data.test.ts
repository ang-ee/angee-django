import { describe, expect, test } from "vitest";

import type { WorkflowGraphEdge, WorkflowGraphStep } from "../documents.console";
import { workflowGraphEdges, workflowGraphNodes } from "./graph-data";

describe("workflowGraphNodes", () => {
  test("uses a neutral node kind for open-registry step classes", () => {
    const nodes = workflowGraphNodes([
      ({
        id: "custom",
        key: "custom",
        name: "Custom",
        step_class: "custom_provider",
        config: {},
        join_rule: "ALL_SUCCESS",
        is_entry: false,
        position: {},
        updated_at: "2026-07-03T00:00:00Z",
      } as unknown as WorkflowGraphStep),
    ]);

    expect(nodes[0]?.kind).toBe("HANDLER");
  });

  test("names run nodes and edges while allowing compact layout to ignore authoring positions", () => {
    const steps = [
      { id: "a", key: "source", name: "Source", step_class: "handler", join_rule: "ALL", is_entry: true, position: { x: 900, y: 800 } },
      { id: "b", key: "target", name: "Target", step_class: "handler", join_rule: "ALL", is_entry: false, position: { x: 1200, y: 800 } },
    ] as unknown as WorkflowGraphStep[];
    const nodes = workflowGraphNodes(steps, new Map([["a", { status: "FAILED", detail: "1 failed · 1 succeeded" }]]), false);
    const edges = workflowGraphEdges([{ id: "edge", condition: "approved", source: steps[0], target: steps[1] }] as unknown as WorkflowGraphEdge[]);

    expect(nodes[0]).toMatchObject({ ariaLabel: "Source · source · 1 failed · 1 succeeded", position: undefined });
    expect(edges[0]).toMatchObject({ ariaLabel: "Source · approved · Target" });
  });
});
