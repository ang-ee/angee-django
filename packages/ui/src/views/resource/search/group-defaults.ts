import * as React from "react";
import type { ResourceViewContextValue } from "../resource-view-context";
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

/** A stack's levels without `GroupSpec.repeated`; the grouping itself stays. */
export function withoutRepeated(stack: readonly ResourceViewGroup[]): readonly ResourceViewGroup[] {
  return stack.map(({ field, granularity }) => ({ field, ...(granularity ? { granularity } : {}) }));
}

/**
 * The view a list renders when its stored stack asks for `GroupSpec.repeated` where
 * the list cannot apply it (`applies` false): a view switch or a hand-written URL
 * left it there, so the list drops the flag (the grouping stays) and repairs the
 * stored stack, after which the URL names what it shows. An authored default that
 * asks for the impossible is kept, for validation to refuse.
 */
export function useAppliedRepeated(resourceView: ResourceViewContextValue, applies: boolean): ResourceViewContextValue {
  const { state, defaultState, setGroupStack } = resourceView;
  const repaired = React.useMemo(() => applies || !state.groupStack.some((level) => level.repeated)
    || serializeResourceViewGroupStack(state.groupStack) === serializeResourceViewGroupStack(defaultState.groupStack)
    ? null : withoutRepeated(state.groupStack), [applies, state.groupStack, defaultState.groupStack]);
  React.useEffect(() => {
    if (repaired) setGroupStack(repaired);
  }, [repaired, setGroupStack]);
  return React.useMemo(() => repaired ? { ...resourceView, state: { ...state, groupStack: repaired } } : resourceView,
    [repaired, resourceView, state]);
}
