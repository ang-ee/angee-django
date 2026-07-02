import { useNamespaceT } from "@angee/ui";
import type { MessageVars } from "@angee/refine";

export const enWorkflowsMessages: Record<string, string> = {
  "workflows.form.definition": "Definition",
  "workflows.form.controls": "Controls",
  "workflows.form.publish": "Publish",
  "workflows.form.start": "Start run",
  "workflows.form.cancel": "Cancel run",
  "workflows.tabs.canvas": "Canvas",
  "workflows.tabs.triggers": "Triggers",
  "workflows.tabs.timeline": "Timeline",
  "workflows.canvas.loading": "Loading workflow graph",
  "workflows.canvas.emptyTitle": "No steps",
  "workflows.canvas.emptyDescription": "Create a step to start the workflow graph.",
  "workflows.canvas.selectStep": "Select a step on the canvas.",
  "workflows.canvas.step": "Step",
  "workflows.canvas.edge": "Edge",
  "workflows.canvas.stepConfig": "Step config",
  "workflows.canvas.edgeConfig": "Edge config",
  "workflows.canvas.draft": "Draft",
  "workflows.canvas.readOnly": "Read-only",
  "workflows.triggers.details": "Trigger",
  "workflows.runs.loading": "Loading run",
  "workflows.runs.emptyTimeline": "No journal rows.",
  "workflows.runs.timeline": "Journal",
  "workflows.runs.graph": "Graph",
  "workflows.runs.input": "Input",
  "workflows.runs.output": "Output",
  "workflows.runs.resume": "Resume",
  "workflows.runs.error": "Error",
  "workflows.inbox.loading": "Loading decisions",
  "workflows.inbox.emptyTitle": "No pending decisions",
  "workflows.inbox.emptyDescription": "Pending workflow decisions assigned to you appear here.",
  "workflows.inbox.payload": "Payload",
  "workflows.inbox.resolution": "Resolution payload",
  "workflows.inbox.complete": "Complete",
  "workflows.inbox.reject": "Reject",
  "workflows.inbox.actionFailed": "Decision failed.",
};

export function useWorkflowsT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("workflows", enWorkflowsMessages);
}
