import type { SlotContribution } from "@angee/ui";
import type { ReactElement } from "react";

/** Optional fields contributed to the native workflow Trigger form. */
export const WORKFLOW_TRIGGER_FORM_FIELDS_SLOT = "workflows.trigger.form-fields";

/** Decision-action content contributed for the shared approval task. */
export const WORKFLOW_DECISION_CONTENT_SLOT = "workflows.decision.content";

/**
 * Typed launch affordances that replace generic entries for claimed lineages.
 *
 * Native slot composition rejects duplicate `(slot, model, id)` identities.
 * Distinct ids that claim an overlapping lineage are rejected by the launch
 * host at render time because the lineage keys live in this slot's opaque
 * content payload.
 */
export const WORKFLOW_LAUNCH_CLAIM_SLOT = "workflows.launch.claim";

export interface WorkflowLaunchClaim {
  /** Unique contribution id within this subject model. */
  id: string;
  /** Canonical model whose saved-record chrome hosts the launch affordance. */
  subjectModel: string;
  /** Stable Workflow keys removed from the generic launch menu. */
  workflowKeys: readonly [string, ...string[]];
  /** Addon-owned typed launch affordance rendered when a claimed lineage is available. */
  content: ReactElement;
  /** Render order among active launch claims; defaults to zero. */
  sequence?: number;
}

/** Private slot payload interpreted by the workflow launch host. */
export interface WorkflowLaunchClaimContent {
  workflowKeys: readonly string[];
  content: ReactElement;
}

/** Declare one model-scoped workflow launch claim through the native slot registry. */
export function workflowLaunchClaim({
  id,
  subjectModel,
  workflowKeys,
  content,
  sequence,
}: WorkflowLaunchClaim): SlotContribution {
  const uniqueKeys = new Set(workflowKeys);
  if (
    uniqueKeys.size !== workflowKeys.length
    || workflowKeys.some((key) => key.trim().length === 0)
  ) {
    throw new Error(
      `Workflow launch claim "${id}" must declare distinct, non-empty workflow keys.`,
    );
  }
  return {
    slot: WORKFLOW_LAUNCH_CLAIM_SLOT,
    model: subjectModel,
    id,
    sequence,
    content: { workflowKeys: [...workflowKeys], content } satisfies WorkflowLaunchClaimContent,
  };
}
