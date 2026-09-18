/** Canonical resource registry keys owned by the projects schema. */
export const PROJECT_MODEL = "projects.Project";
export const MILESTONE_MODEL = "projects.Milestone";
export const TASK_MODEL = "projects.Task";
export const PARTICIPANT_MODEL = "projects.Participant";

/**
 * Task lifecycle tones. The shared vocabulary reads `open` as live-and-healthy,
 * which is right for a project but wrong for a task: it puts `open` and `done`
 * in the same green, so a list of tasks shows no progress at a glance. A task's
 * `open` is in-flight, not finished. Declared per column rather than by editing
 * the shared map, which every other model reads.
 */
export const TASK_STATUS_TONES = {
  open: "info",
  done: "success",
  dropped: "neutral",
} as const;
