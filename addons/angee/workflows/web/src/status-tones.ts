import type { Tone } from "@angee/ui";

/** The run-state words workflows adds to the composed status vocabulary: a declared node
 * without an execution row is neutral rather than waiting; a hold is attention. The native
 * step statuses (skipped, canceled, …) keep the framework's tones. */
export const WORKFLOW_STATUS_TONES = { unreached: "neutral", plan_running: "neutral", plan_complete: "success", decision: "warning", run: "warning" } satisfies Record<string, Tone>;
