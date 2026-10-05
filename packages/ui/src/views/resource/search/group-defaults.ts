import type { ResourceViewDefaultGroups, ResourceViewGroup } from "../model/filter";
import { normaliseGroupStack, serializeResourceViewGroupStack } from "../model/search";
import { createResourceViewState, type ResourceViewState } from "../model/state";
import type { UiTranslate } from "../../../i18n";

/** Normalize list declarations once for provider defaults and catalog axis seeding. */
export function declaredGroupDefaults(
  defaultGroup: ResourceViewGroup | readonly ResourceViewGroup[] | null | undefined,
  defaultGroups?: ResourceViewDefaultGroups,
) {
  const stack = (group: typeof defaultGroup) => normaliseGroupStack(group == null ? [] : Array.isArray(group) ? group : [group]);
  const groupStack = stack(defaultGroup);
  const groupStacks = Object.fromEntries(Object.entries(defaultGroups ?? {}).map(([view, group]) => [view, stack(group)]));
  return { groupStack, groupStacks, groups: [...groupStack, ...Object.values(groupStacks).flat()] };
}

/** An ambient provider must own the same declared default, even after user edits. */
export function validateDeclaredGroupDefaults(
  name: string,
  defaultGroup: Parameters<typeof declaredGroupDefaults>[0],
  defaultGroups: Parameters<typeof declaredGroupDefaults>[1],
  defaultState: Readonly<ResourceViewState>,
  t: UiTranslate,
): void {
  if (defaultGroup === undefined && defaultGroups === undefined) return;
  const declared = createResourceViewState({ ...declaredGroupDefaults(defaultGroup, defaultGroups), view: defaultState.view }).groupStack;
  if (serializeResourceViewGroupStack(declared) !== serializeResourceViewGroupStack(defaultState.groupStack)) {
    throw new Error(t("list.defaultGroupingMismatch", {
      name, declared: JSON.stringify(declared), ambient: JSON.stringify(defaultState.groupStack),
    }));
  }
}
