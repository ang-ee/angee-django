import * as React from "react";

import { definitionEdit, type WorkflowDefinitionValues } from "./workflow-definition-state";

export interface WorkflowInputPreviewRequest {
  workflow: string;
  expectedRevision: number;
  edit: Record<string, unknown>;
  target: { id: string } | { client_key: string };
}

interface WorkflowInputPreviewOwner {
  prepare: (targetIdentity: string) => WorkflowInputPreviewRequest | null;
  stale: () => void;
}

const Context = React.createContext<WorkflowInputPreviewOwner | null>(null);
export const WorkflowInputPreviewProvider = Context.Provider;
export function useWorkflowInputPreview(): WorkflowInputPreviewOwner | null { return React.useContext(Context); }

export function inputPreviewRequest(
  workflow: string,
  baseline: WorkflowDefinitionValues,
  current: WorkflowDefinitionValues,
  targetIdentity: string,
): WorkflowInputPreviewRequest | null {
  const target = current.definition.nodes[targetIdentity];
  if (!target) return null;
  return {
    workflow,
    expectedRevision: baseline.definition.revision,
    edit: definitionEdit(baseline, current),
    target: target.id ? { id: target.id } : { client_key: target.clientKey ?? targetIdentity },
  };
}
