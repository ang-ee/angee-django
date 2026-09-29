/** Validate declaration ids against the inventory, before visibility or implementation matching. */
export function assertContributionIds(
  admit: readonly string[] | undefined,
  entries: readonly { id: string }[],
  owner: string,
): void {
  if (admit === undefined) return;
  const known = new Set(entries.map((entry) => entry.id));
  for (const id of admit) {
    if (!known.has(id)) throw new Error(`${owner} admits unknown contribution id "${id}".`);
  }
}

/** Omission admits all; an empty list admits none. Preserve the owner's ordering. */
export function admittedContributions<T extends { id: string }>(
  entries: readonly T[],
  admit: readonly string[] | undefined,
): readonly T[] {
  return admit === undefined ? entries : entries.filter((entry) => admit.includes(entry.id));
}
