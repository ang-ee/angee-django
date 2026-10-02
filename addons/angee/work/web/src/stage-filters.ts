/** The system-owned stage categories excluded from ordinary planning surfaces. */
const SYSTEM_STAGE_CATEGORIES = ["triage", "duplicate"] as const;

/** Same-queue stages rendered as ordinary board lanes. */
export function queueStageFilters(queueId: string) {
  return [
    { field: "queue", operator: "eq" as const, value: queueId },
    { field: "category", operator: "ne" as const, value: SYSTEM_STAGE_CATEGORIES[0] },
    { field: "category", operator: "ne" as const, value: SYSTEM_STAGE_CATEGORIES[1] },
  ];
}

/** Same-queue stages that accept may enter by hand. */
export function acceptStageFilters(queueId: string) {
  return [
    ...queueStageFilters(queueId),
    { field: "rule_owned", operator: "eq" as const, value: false },
    { field: "conceals", operator: "eq" as const, value: false },
  ];
}
