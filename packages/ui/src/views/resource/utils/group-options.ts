import { ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import type { ResourceToolbarGroupOption } from "../../../toolbars";
import type { ResourceViewGroup } from "../resource-view-model";
import type { ColumnDescriptor } from "../../page";
import { resourceFieldGroupLabel } from "../model-metadata-defaults";
import { queryForColumns } from "../resource-query";

/** Render choices from the query's declared axes; identity never comes from labels. */
export function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  defaultGroups: ResourceViewGroup | readonly ResourceViewGroup[] | null | undefined,
  suppliedQuery?: ResourceQuery,
): readonly ResourceToolbarGroupOption[] {
  const declared = defaultGroups ? Array.isArray(defaultGroups) ? defaultGroups : [defaultGroups] : [];
  const query = suppliedQuery ?? queryForColumns(columns, metadata, declared);
  const defaults = query.groupsFrom(declared).map((axis) => axis.spec);
  const names = [...new Set([...defaults.map(({ field }) => field), ...Object.keys(query.axes)])];
  return names.map((name) => {
    const axis = query.axis(name);
    const declaration = query.axes[name]!;
    const initial = defaults.find(({ field }) => field === name);
    const granularities = declaration.extractions.map(({ name }) => name);
    const date = declaration.kind === "date" || granularities.length > 0;
    return {
      id: name,
      label: columns.find((column) => column.field === name || column.field === declaration.labelPath)?.header
        ?? resourceFieldGroupLabel(name, metadata?.fields[name]),
      group: initial ?? { ...axis.spec, ...(date && granularities.includes("day") ? { granularity: "day" } : {}) },
      type: date ? "date" as const : "value" as const,
      ...(date ? { granularities } : {}),
    };
  });
}

export function resolveResourceViewGroup(group: ResourceViewGroup, metadata: ModelMetadata | null): ResourceViewGroup {
  return metadata ? ResourceQuery.from(metadata).group(group).spec : group;
}

/** A malformed group is a view error, never silently removed from the request. */
export function validResourceViewGroupStack(
  groups: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null,
): readonly ResourceViewGroup[] {
  return metadata ? ResourceQuery.from(metadata).groupsFrom(groups).map((axis) => axis.spec) : groups;
}
