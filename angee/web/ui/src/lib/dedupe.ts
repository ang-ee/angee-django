export function dedupeBy<TItem, TKey>(
  items: Iterable<TItem>,
  keyOf: (item: TItem) => TKey,
): TItem[] {
  const seen = new Set<TKey>();
  const result: TItem[] = [];
  for (const item of items) {
    const key = keyOf(item);
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}
