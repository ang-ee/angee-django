import type { Tone } from "@angee/ui";

/** A declared node without an execution row is neutral, rather than waiting. */
export const WORKFLOW_STATUS_TONES = { unreached: "neutral", plan_running: "neutral", plan_complete: "success", decision: "warning", run: "warning" } satisfies Record<string, Tone>;

/** Step overrides cover graph tokens and native enum values for exact-case tone lookup. */
export const WORKFLOW_STEP_STATUS_TONES: Record<string, Tone> = Object.fromEntries(
  Object.entries({ skipped: "info", canceled: "warning" } satisfies Record<string, Tone>)
    .flatMap(([value, tone]) => [[value, tone] as const, [value.toUpperCase(), tone] as const]),
);
