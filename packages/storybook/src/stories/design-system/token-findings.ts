// Inventory §1.4–1.5, reconciled on 2026-10-20: the unused tokens, bridges and
// unresolved classes were deleted or repaired at their owners; the catalog guard
// keeps these lists empty.
export const noOwner = new Set<string>([]);

export const unresolvedClasses = [] as const;
