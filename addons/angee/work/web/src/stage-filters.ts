/** The system-owned stage categories excluded from ordinary planning surfaces. */
export const SYSTEM_STAGE_CATEGORIES = ["triage", "duplicate"] as const;

/** Same-queue, user-managed stages offered by accept and rendered as board lanes. */
export function queueStageFilters(queueId: string) {
  return [
    { field: "queue", operator: "eq" as const, value: queueId },
    { field: "category", operator: "ne" as const, value: SYSTEM_STAGE_CATEGORIES[0] },
    { field: "category", operator: "ne" as const, value: SYSTEM_STAGE_CATEGORIES[1] },
  ];
}
