export const WORKFLOW_STEP_USAGE_FIELDS = [
  "workflow.id",
  "workflow.name",
  "workflow.version",
  "workflow.status",
] as const;

export interface WorkflowStepUsage {
  id: string;
  name: string;
  version: number;
  status: string;
}

/** Present one workflow identity consistently wherever Step usage is listed. */
export function workflowLabel(workflow: WorkflowStepUsage): string {
  return `${workflow.name} · v${workflow.version} · ${workflow.status}`;
}

/** Read the relation shape selected by the shared Step usage fields. */
export function workflowUsage(value: unknown): WorkflowStepUsage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  return typeof row.id === "string"
    && typeof row.name === "string"
    && typeof row.version === "number"
    && typeof row.status === "string"
    ? { id: row.id, name: row.name, version: row.version, status: row.status }
    : null;
}
