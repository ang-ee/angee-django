export interface WorkflowEditorSelection {
  stepId: string | null;
  edgeId: string | null;
  narrowView: "canvas" | "inspector";
}

export const EMPTY_WORKFLOW_EDITOR_SELECTION: WorkflowEditorSelection = {
  stepId: null,
  edgeId: null,
  narrowView: "canvas",
};

export type WorkflowEditorSelectionEvent =
  | { type: "select-step"; id: string }
  | { type: "select-edge"; id: string }
  | { type: "clear" }
  | { type: "show-canvas" }
  | { type: "show-inspector" };

/** Owns responsive pane navigation independently from the mounted selection. */
export function workflowEditorSelection(
  state: WorkflowEditorSelection,
  event: WorkflowEditorSelectionEvent,
): WorkflowEditorSelection {
  switch (event.type) {
    case "select-step":
      return { stepId: event.id, edgeId: null, narrowView: "inspector" };
    case "select-edge":
      return { stepId: null, edgeId: event.id, narrowView: "inspector" };
    case "clear":
      return EMPTY_WORKFLOW_EDITOR_SELECTION;
    case "show-canvas":
      return { ...state, narrowView: "canvas" };
    case "show-inspector":
      return state.stepId || state.edgeId ? { ...state, narrowView: "inspector" } : state;
  }
}
