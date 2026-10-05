import type { Tone } from "@angee/ui";

/** A declared node without an execution row is neutral, rather than waiting. */
export const WORKFLOW_STATUS_TONES = { unreached: "neutral", plan_running: "neutral", plan_complete: "success", decision: "warning", run: "warning" } satisfies Record<string, Tone>;
