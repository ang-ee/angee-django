/** Canonical resource registry keys owned by the work schema. */
export const QUEUE_MODEL = "work.Queue";
export const STAGE_MODEL = "work.Stage";
export const CYCLE_MODEL = "work.Cycle";

/**
 * Tones for the stage categories the shared status vocabulary does not know,
 * matching `Stage.PROVISIONED_STAGES` in the work models. `started`,
 * `completed` and `canceled` already read from `STATUS_TONES`.
 */
export const STAGE_CATEGORY_TONES = {
  triage: "warning",
  backlog: "neutral",
  unstarted: "brand",
  duplicate: "neutral",
} as const;
