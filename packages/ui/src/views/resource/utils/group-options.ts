import type { ReactNode } from "react";
import { groupAllowedByResource, groupSupportedByResource, isRelationLabelAxis, isToOneRelationField, relationFilterForRelation, resourceGroupDimensionForField, type ModelMetadata, type Row } from "@angee/metadata";
import type { ResourceToolbarGroupOption } from "../../../toolbars";
import { RESOURCE_VIEW_GROUP_GRANULARITIES, type ResourceViewGroup } from "../resource-view-model";
import type { ColumnDescriptor } from "../../page";
import { fieldLabel, resourceFieldGroupLabel } from "../model-metadata-defaults";
import { dateGroupType, supportsChoiceFacet } from "./filter-options";
export function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
  defaultGroups: ResourceViewGroup | readonly ResourceViewGroup[] | null | undefined,
): readonly ResourceToolbarGroupOption[] {
  const options: ResourceToolbarGroupOption[] = [];
  const seen = new Set<string>();
  // One relation is one group, however it was reached — a declared default, its
  // own `groupByFields` entry, or a column naming its label path all resolve to
  // the same `aggregateField`, so the first spelling wins and the rest collapse.
  const seenRelations = new Set<string>();
  const addOption = (option: ResourceToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    const relation = option.group.aggregateField;
    if (relation && seenRelations.has(relation)) return;
    seen.add(option.id);
    if (relation) seenRelations.add(relation);
    options.push(option);
  };

  for (const defaultGroup of defaultGroupList(defaultGroups)) {
    const resolvedGroup = resolveResourceViewGroup(defaultGroup, metadata);
    if (!groupAllowedByResource(resolvedGroup, metadata)) continue;
    const field = metadata?.fields[resolvedGroup.field];
    const type = dateGroupType(resolvedGroup.field, field) ? "date" : "value";
    addOption({
      id: resolvedGroup.field,
      label: relationGroupLabel(resolvedGroup, metadata)
        ?? resourceFieldGroupLabel(resolvedGroup.field, field),
      group: resolvedGroup,
      type,
      ...(type === "date" ? { granularities: RESOURCE_VIEW_GROUP_GRANULARITIES } : {}),
    });
  }

  // A column that names a relation's label path owns that relation's option — its
  // header names it. The groupByFields pass only fills in relations no column
  // reaches (a view need not carry a column to group by a declared relation).
  const columnRelations = new Set(
    columns
      .map((column) => relationGroupForFieldPath(column.field, metadata)?.aggregateField)
      .filter((relation): relation is string => relation != null),
  );
  const resourceGroupByFields = metadata?.resource?.groupByFields ?? [];
  const groupAliases = metadata?.resource?.groupAliases ?? [];
  const aliasedAggregateFields = new Set(
    groupAliases.map((alias) => alias.aggregateField),
  );
  for (const alias of groupAliases) {
    if (!metadata) continue;
    const group = groupAliasToResourceViewGroup(alias);
    if (!groupAllowedByResource(group, metadata)) continue;
    const field = metadata.fields[alias.field];
    if (!field) continue;
    const type = dateGroupType(alias.field, field) ? "date" : "value";
    addOption({
      id: alias.field,
      label: resourceFieldGroupLabel(alias.field, field),
      group,
      type,
      ...(type === "date" ? { granularities: RESOURCE_VIEW_GROUP_GRANULARITIES } : {}),
    });
  }
  for (const fieldName of resourceGroupByFields) {
    if (!metadata) continue;
    if (aliasedAggregateFields.has(fieldName)) continue;
    const field = metadata.fields[fieldName];
    const dimension = resourceGroupDimensionForField(fieldName, metadata);
    if (!field && !dimension) continue;
    // A to-one relation groups by its identity, labelled by the label axis it
    // carries — however the node projects it (object or bare `ID`).
    const relationGroup = relationGroupForRelationField(fieldName, metadata);
    if (relationGroup) {
      if (!columnRelations.has(fieldName)) {
        addOption({
          id: fieldName,
          label: fieldLabel(fieldName, field),
          group: relationGroup,
          type: "value",
        });
      }
      continue;
    }
    // A relation the resource declares no filter for cannot be grouped by
    // identity, and its raw id is not a group a reader wants.
    if (isToOneRelationField(field)) continue;
    // A relation's label axis is not a group of its own — it rides along with the
    // relation group above, which is the one that can drill down.
    if (isRelationLabelAxis(fieldName, metadata)) continue;
    const type = dateGroupType(fieldName, field) ? "date" : "value";
    addOption({
      id: fieldName,
      label: resourceFieldGroupLabel(fieldName, field),
      group: {
        field: fieldName,
        ...(type === "date" ? { granularity: "day" as const } : {}),
      },
      type,
      ...(type === "date" ? { granularities: RESOURCE_VIEW_GROUP_GRANULARITIES } : {}),
    });
  }

  for (const column of columns) {
    const field = metadata?.fields[column.field];
    const relationGroup = relationGroupOptionForColumn(column, metadata);
    if (relationGroup && groupAllowedByResource(relationGroup.group, metadata)) {
      addOption(relationGroup);
      continue;
    }
    if (!groupAllowedByResource({ field: column.field }, metadata)) continue;
    if (dateGroupType(column.field, field)) {
      addOption({
        id: column.field,
        // Group labels are field-derived (groupFieldLabel trims a trailing
        // "At"), independent of the column's display header.
        label: resourceFieldGroupLabel(column.field, field),
        group: { field: column.field, granularity: "day" },
        type: "date",
        granularities: RESOURCE_VIEW_GROUP_GRANULARITIES,
      });
      continue;
    }
    if (supportsChoiceFacet(column, metadata)) {
      addOption({
        id: column.field,
        label: resourceFieldGroupLabel(column.field, field),
        group: { field: column.field },
        type: "value",
      });
    }
  }

  return options;
}

function relationGroupOptionForColumn<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  metadata: ModelMetadata | null,
): ResourceToolbarGroupOption | null {
  const group = relationGroupForFieldPath(column.field, metadata);
  if (!group) return null;
  const [relationField] = column.field.split(".");
  const field = relationField ? metadata?.fields[relationField] : undefined;
  return {
    id: column.field,
    label: fieldLabel(relationField ?? column.field, field, column.header),
    group,
    type: "value",
  };
}

/**
 * The group that buckets rows by one to-one relation: the resource's declared
 * relation filter owns the axis (`aggregateField`) and its identity key
 * (`aggregateKey`), so a bucket drills down through the relation's public id.
 */
function relationGroupForRelationField(
  relationField: string,
  metadata: ModelMetadata | null,
): ResourceViewGroup | null {
  // The declared relation filter is the proof: the resource only carries one for
  // a relation its `relationAxes` name, whichever way the node projects the FK —
  // or whether it projects it at all.
  const filter = relationFilterForRelation(relationField, metadata);
  if (!filter?.aggregateKey) return null;
  return {
    field: relationField,
    aggregateField: filter.field,
    aggregateKey: filter.aggregateKey,
  };
}

function relationGroupForFieldPath(
  fieldPath: string,
  metadata: ModelMetadata | null,
): ResourceViewGroup | null {
  const [relationField, labelField, ...rest] = fieldPath.split(".");
  if (!relationField || !labelField || rest.length > 0) return null;
  const group = relationGroupForRelationField(relationField, metadata);
  // The caller named the relation by its label path; keep that as the display
  // field, the axis it groups on is the relation either way.
  return group ? { ...group, field: fieldPath } : null;
}

function relationGroupLabel(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ReactNode | null {
  if (!group.aggregateField) return null;
  const [relationField, labelField, ...rest] = group.field.split(".");
  if (!relationField || !labelField || rest.length > 0) return null;
  const field = metadata?.fields[relationField];
  return isToOneRelationField(field) ? fieldLabel(relationField, field) : null;
}

export function resolveResourceViewGroup(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ResourceViewGroup {
  if (group.aggregateField && group.aggregateKey) {
    return canonicalResourceViewGroup(group, metadata);
  }
  const aliasGroup = groupAliasForField(group.field, metadata);
  if (aliasGroup) return canonicalResourceViewGroup({ ...group, ...aliasGroup }, metadata);
  const field = metadata?.fields[group.field];
  const relationGroup =
    relationGroupForFieldPath(group.field, metadata)
    ?? (
      isToOneRelationField(field)
        ? relationGroupForRelationField(group.field, metadata)
        : null
    );
  return canonicalResourceViewGroup(
    relationGroup ? { ...group, ...relationGroup } : group,
    metadata,
  );
}

export function validResourceViewGroupStack(
  groupStack: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null,
): readonly ResourceViewGroup[] {
  return groupStack.flatMap((group) => {
    const resolvedGroup = resolveResourceViewGroup(group, metadata);
    return groupSupportedByResource(resolvedGroup, metadata)
      ? [resolvedGroup]
      : [];
  });
}

function groupAliasForField(
  field: string,
  metadata: ModelMetadata | null,
): ResourceViewGroup | null {
  const alias = metadata?.resource?.groupAliases?.find((item) => item.field === field);
  return alias ? groupAliasToResourceViewGroup(alias) : null;
}

function groupAliasToResourceViewGroup(alias: {
  field: string;
  aggregateField: string;
  aggregateKey: string;
}): ResourceViewGroup {
  return {
    field: alias.field,
    aggregateField: alias.aggregateField,
    aggregateKey: alias.aggregateKey,
  };
}

function canonicalResourceViewGroup(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): ResourceViewGroup {
  const dimension = resourceGroupDimensionForField(
    group.aggregateField ?? group.field,
    metadata,
  );
  if (!dimension) return group;
  if (group.aggregateField) {
    return dimension.field === group.aggregateField
      ? group
      : { ...group, aggregateField: dimension.field };
  }
  return dimension.field === group.field ? group : { ...group, field: dimension.field };
}

function defaultGroupList(
  defaultGroups: ResourceViewGroup | readonly ResourceViewGroup[] | null | undefined,
): readonly ResourceViewGroup[] {
  if (!defaultGroups) return [];
  return isResourceViewGroupList(defaultGroups) ? defaultGroups : [defaultGroups];
}

function isResourceViewGroupList(
  value: ResourceViewGroup | readonly ResourceViewGroup[],
): value is readonly ResourceViewGroup[] {
  return Array.isArray(value);
}
