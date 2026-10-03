/** A sibling the ordering rule places: an id, an optional sequence and anchor, and its declaration index. */
export interface Positioned {
  id: string;
  sequence?: number;
  before?: string;
  after?: string;
}

/**
 * Order one list of siblings: by sequence (missing sorts last), then input
 * order. A sibling with `before`/`after` is then placed next to its anchor when
 * the anchor is in the same list (siblings on one anchor keep that order); an
 * anchor elsewhere leaves the sibling in place. Anchors that depend on each
 * other in a cycle throw.
 */
export function positionSiblings<T extends Positioned>(siblings: readonly T[], describe = "Items"): T[] {
  const sequenceOf = (item: T): number => item.sequence ?? Number.POSITIVE_INFINITY;
  const sorted = siblings.map((item, index) => ({ item, index }))
    .sort((left, right) => sequenceOf(left.item) - sequenceOf(right.item) || left.index - right.index)
    .map(({ item }) => item);
  const present = new Set(sorted.map((item) => item.id));
  const anchorOf = (item: T): { id: string; before: boolean } | undefined => {
    const id = item.before ?? item.after;
    return id !== undefined && present.has(id) ? { id, before: item.before !== undefined } : undefined;
  };
  const result = sorted.filter((item) => !anchorOf(item));
  let pending = sorted.filter((item) => anchorOf(item));
  const lastAfter = new Map<string, string>();
  while (pending.length) {
    const placeable = pending.filter((item) => result.some((placed) => placed.id === anchorOf(item)!.id));
    if (!placeable.length) {
      throw new Error(`${describe} ${pending.map((item) => `"${item.id}"`).join(", ")} position themselves in a before/after cycle.`);
    }
    for (const item of placeable) {
      const anchor = anchorOf(item)!;
      if (anchor.before) {
        result.splice(result.findIndex((placed) => placed.id === anchor.id), 0, item);
      } else {
        const last = lastAfter.get(anchor.id) ?? anchor.id;
        result.splice(result.findIndex((placed) => placed.id === last) + 1, 0, item);
        lastAfter.set(anchor.id, item.id);
      }
    }
    pending = pending.filter((item) => !placeable.includes(item));
  }
  return result;
}

/** Siblings in id order: the deterministic input order containers position from, never addon order. */
export function orderById<T extends { id: string }>(siblings: readonly T[]): T[] {
  return [...siblings].sort((left, right) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0));
}
