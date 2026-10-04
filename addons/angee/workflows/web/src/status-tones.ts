import type { Tone } from "@angee/ui";

/** A declared node without an execution row is neutral, rather than waiting. */
export const WORKFLOW_STATUS_TONES = { unreached: "neutral" } satisfies Record<string, Tone>;

/** Graph-local overrides use the native resolver; shared status words cannot be claimed globally. */
export const WORKFLOW_GRAPH_STATUS_TONES = {
  ...WORKFLOW_STATUS_TONES, skipped: "info", canceled: "warning",
} satisfies Record<string, Tone>;
