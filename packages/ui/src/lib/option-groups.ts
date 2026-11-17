/**
 * Options under their picker headings, the one grouping rule for every picker
 * (`Select`, the search panel's filter sections and facets, the relation
 * multi-picker). A group is the options sharing a `group` heading. Groups with a
 * `groupOrder` come first, by that order; every other group, the ungrouped
 * options included (`label: null`), follows in first-appearance order. Options
 * keep their given order within a group. A renderer heads the ungrouped bucket
 * "Other" (`options.otherGroup`) only when it follows a labelled group.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function optionGroups<TOption extends { group?: string; groupOrder?: number }>(
  options: readonly TOption[],
): readonly { label: string | null; options: readonly TOption[] }[] {
  const groups = new Map<string, { label: string | null; order: number; first: number; options: TOption[] }>();
  options.forEach((option, index) => {
    const key = option.group ? `group:${option.group}` : "";
    const group = groups.get(key)
      ?? { label: option.group || null, order: option.groupOrder ?? Number.POSITIVE_INFINITY, first: index, options: [] };
    group.options.push(option);
    groups.set(key, group);
  });
  return [...groups.values()]
    .sort((left, right) => left.order === right.order ? left.first - right.first : left.order < right.order ? -1 : 1)
    .map(({ label, options: grouped }) => ({ label, options: grouped }));
}
