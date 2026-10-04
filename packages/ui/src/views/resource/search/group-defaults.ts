import { RESOURCE_VIEW_KINDS, type ResourceViewDefaultGroups, type ResourceViewGroup, type ResourceViewKind } from "../resource-view-model";

/** Resolve the existing single-group declarations across view kinds. */
export function defaultGroupForView(
  defaultGroup: ResourceViewGroup | null | undefined,
  defaultGroups: ResourceViewDefaultGroups | undefined,
  view: ResourceViewKind,
): ResourceViewGroup | null {
  if (defaultGroups && Object.prototype.hasOwnProperty.call(defaultGroups, view)) return defaultGroups[view] ?? null;
  return defaultGroup ?? null;
}

export function defaultGroupsForToolbar(
  defaultGroup: ResourceViewGroup | null | undefined,
  defaultGroups: ResourceViewDefaultGroups | undefined,
): readonly ResourceViewGroup[] {
  return [
    ...(defaultGroup ? [defaultGroup] : []),
    ...RESOURCE_VIEW_KINDS.flatMap((view) => defaultGroups?.[view] ? [defaultGroups[view]!] : []),
  ];
}
