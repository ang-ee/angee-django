import { describe, expect, test } from "vitest";
import { EMPTY_WORKFLOW_EDITOR_SELECTION, workflowEditorSelection } from "./workflow-editor-state";

describe("workflow editor responsive state", () => {
  test("Back hides the inspector without discarding its mounted selection", () => {
    const selected = workflowEditorSelection(EMPTY_WORKFLOW_EDITOR_SELECTION, { type: "select-step", id: "step_1" });
    const canvas = workflowEditorSelection(selected, { type: "show-canvas" });
    expect(canvas).toEqual({ stepId: "step_1", edgeId: null, narrowView: "canvas" });
    expect(workflowEditorSelection(canvas, { type: "show-inspector" })).toEqual(selected);
  });
});
