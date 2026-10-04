import type { Tone } from "@angee/ui";

/** A declared node without an execution row is neutral, rather than waiting. */
export const WORKFLOW_STATUS_TONES = { unreached: "neutral" } satisfies Record<string, Tone>;
