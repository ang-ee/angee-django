import { describe, expect, test } from "vitest";

import type { WorkflowGraphStep } from "../documents.console";
import { workflowGraphNodes } from "./graph-data";

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
});
