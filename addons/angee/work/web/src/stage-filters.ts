/** Same-queue stages rendered as ordinary board lanes, including rule-owned lanes. */
export function queueStageFilters(queueId: string) {
  return [
    { field: "queue", operator: "eq" as const, value: queueId },
    { field: "is_system", operator: "eq" as const, value: false },
  ];
}

/** Acceptance also requires the owning rule to admit manual entry. */
export function acceptStageFilters(queueId: string) {
  return [
    { field: "queue", operator: "eq" as const, value: queueId },
    { field: "accepts_manual_entry", operator: "eq" as const, value: true },
  ];
}
