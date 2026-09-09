import { QueryParseError, ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
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
  // Defaults may use an axis alias (a label or identity path); canonicalise them
  // and drop any that resolve to nothing so one stale default cannot break the toolbar.
  const defaults = declared.flatMap((group) => {
    const field = query.canonicalAxisField(group.field);
    if (field === undefined) {
      warnUnresolvedGroup(group, metadata);
      return [];
    }
    return [{ ...group, field }];
  });
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

function warnUnresolvedGroup(group: ResourceViewGroup, metadata: ModelMetadata | null, error?: unknown): void {
  const detail = error instanceof Error ? error.message : "unknown group axis";
  console.warn(
    `Dropping group "${group.field}" on ${metadata?.resource?.name ?? "resource"}: ${detail}. `
    + "Declared axes: " + (metadata ? Object.keys(ResourceQuery.from(metadata).axes).join(", ") : "n/a"),
  );
}

/**
 * Resolve a declared default group, or return null when it no longer names an
 * axis. Page defaults and persisted view state outlive schema changes; an
 * unresolvable group is reported and dropped rather than failing the whole view.
 */
export function resolveResourceViewGroupSoft(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ResourceViewGroup | null {
  try {
    return resolveResourceViewGroup(group, metadata);
  } catch (error) {
    if (!(error instanceof QueryParseError)) throw error;
    warnUnresolvedGroup(group, metadata, error);
    return null;
  }
}

/** Keep every group that still resolves; drop (and report) the ones that do not. */
export function validResourceViewGroupStackSoft(
  groups: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null,
): readonly ResourceViewGroup[] {
  if (!metadata) return groups;
  const kept = groups.flatMap((group) => {
    const resolved = resolveResourceViewGroupSoft(group, metadata);
    return resolved ? [resolved] : [];
  });
  if (kept.length === groups.length) return validResourceViewGroupStack(kept, metadata);
  try {
    return validResourceViewGroupStack(kept, metadata);
  } catch (error) {
    if (!(error instanceof QueryParseError)) throw error;
    console.warn(`Dropping group stack on ${metadata.resource?.name ?? "resource"}: ${error.message}`);
    return [];
  }
}
