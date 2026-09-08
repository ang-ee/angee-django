import { describe, expect, test } from "vitest";

import type { WorkflowDefinitionValues } from "./workflow-definition-state";
import { historyRebaser, type EditableFrame } from "./workflow-definition-history";

const persisted = { id: "step_1", name: "First", key: "first", step_class: "gate", config: {}, config_errors: {}, join_rule: "ALL_SUCCESS", is_entry: true, position: {} } as never;
const created = { id: "", clientKey: "new", name: "New", key: "new", step_class: "gate", config: {}, config_errors: {}, join_rule: "ALL_SUCCESS", is_entry: false, position: {} } as never;
function values(nodes: Record<string, never>, edges: Record<string, never> = {}): WorkflowDefinitionValues {
  return { id: "workflow_1", key: "flow", name: "Flow", description: "", purpose: "AUTOMATION", subject_declaration: "", status: "DRAFT", version: 1, lineage_id: "workflow_1", error_workflow: null, max_steps: 10, budget: {}, definition: { revision: 2, nodes, edges, readiness: [] } };
}

describe("workflow definition history correlation rebase", () => {
  test("updates saved creations and gives restored deletions fresh client identities", () => {
    const oldRoute = { id: "edge_1", source: "step_1", target: "new", condition: "", clientKey: undefined } as never;
    const baseline = values({ step_1: persisted }, { old_route: oldRoute });
    const submitted = values({ new: created });
    const frame: EditableFrame = {
      workflow: { name: "Flow" },
      nodes: { step_1: persisted, new: created },
      edges: { old_route: oldRoute },
    };
    const rebased = historyRebaser(baseline, submitted, { nodes: [{ client_key: "new", id: "step_2" }], edges: [] })(frame);
    expect(rebased.nodes.new?.id).toBe("step_2");
    const restored = Object.entries(rebased.nodes).find(([, node]) => node.name === "First")!;
    expect(restored[0]).toMatch(/^node-/);
    expect(restored[1]).toMatchObject({ id: "", clientKey: restored[0] });
    expect(Object.values(rebased.edges)[0]).toMatchObject({ id: "", source: restored[0], target: "new" });
  });
});
