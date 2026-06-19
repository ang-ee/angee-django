import * as React from "react";
import {
  useModelMetadata,
  useSchemaFieldMetadata,
  type ModelRelationFilterMetadata,
  type ModelRelationFilterMode,
} from "@angee/sdk";

import type {
  DataToolbarFilterField,
  DataToolbarFilterOption,
  DataToolbarGroupOption,
} from "../toolbars";
import type { DataViewFilter, DataViewGroup } from "./data-view-model";
import {
  relationFieldInfo,
  type RelationFieldInfo,
} from "./model-metadata-defaults";
import { useRelationOptions } from "./relation-options";

const RELATION_FACET_OPTION_LIMIT = 200;
const EMPTY_FILTER_OPTIONS: readonly DataToolbarFilterOption[] = [];
const EMPTY_FILTER_FIELDS: readonly DataToolbarFilterField[] = [];

export interface RelationFacetOptions {
  /** Relation field on the current model, e.g. `provider`. */
  field: string;
  /** Toolbar label; defaults to the relation field name. */
  label?: React.ReactNode;
  /** Filter field accepted by the current model filter input; defaults to SDL metadata. */
  filterField?: string;
  /** Filter value shape; defaults to SDL metadata, then lookup for explicit fields. */
  filterMode?: ModelRelationFilterMode;
  /** Aggregate bucket key returned by the API; defaults to SDL metadata. */
  aggregateKey?: string;
  /** Related-record display field; defaults to the related model representation. */
  labelField?: string;
  /** Related rows fetched for the facet picker. */
  pageSize?: number;
  /** Custom group axis; `false` suppresses group option generation. */
  group?: DataViewGroup | false;
}

export interface RelationFacet {
  filters: readonly DataToolbarFilterOption[];
  filterFields: readonly DataToolbarFilterField[];
  groupOption?: DataToolbarGroupOption;
}

/** Build toolbar filters/groups for a to-one relation using schema metadata. */
export function useRelationFacet(
  model: string,
  options: RelationFacetOptions,
): RelationFacet {
  const {
    aggregateKey: optionAggregateKey,
    field,
    filterField: optionFilterField,
    filterMode: optionFilterMode,
    group,
    label: optionLabel,
    labelField: optionLabelField,
    pageSize = RELATION_FACET_OPTION_LIMIT,
  } = options;
  const schemaMetadata = useSchemaFieldMetadata();
  const modelMetadata = useModelMetadata(model);
  const relation = React.useMemo(
    () => relationFieldInfo(field, modelMetadata, schemaMetadata),
    [field, modelMetadata, schemaMetadata],
  );
  const labelField = optionLabelField ?? relation?.labelField ?? "id";
  const { options: choiceOptions } = useRelationOptions(relation, {
    labelField,
    pageSize,
    sort: true,
  });
  const filter = React.useMemo(
    () =>
      relationFilterConfig(relation?.filter, {
        field: optionFilterField,
        mode: optionFilterMode,
      }),
    [optionFilterField, optionFilterMode, relation],
  );
  const aggregateKey = optionAggregateKey ?? filter?.aggregateKey;
  const label = optionLabel ?? relationLabel(field);
  const filters = React.useMemo<readonly DataToolbarFilterOption[]>(
    () =>
      relation && filter
        ? choiceOptions.map((option) => ({
            id: `${filter.field}:${option.value}`,
            label: option.label,
            chipLabel: option.label,
            filter: relationFacetFilter(filter, option.value),
          }))
        : EMPTY_FILTER_OPTIONS,
    [choiceOptions, filter, relation],
  );
  const filterFields = React.useMemo<readonly DataToolbarFilterField[]>(
    () =>
      relation && filter?.mode === "lookup"
        ? [{
            id: filter.field,
            field: filter.field,
            label,
            type: "selection",
            options: choiceOptions,
          }]
        : EMPTY_FILTER_FIELDS,
    [choiceOptions, filter, label, relation],
  );
  const groupOption = React.useMemo(
    () =>
      relationGroupOption({
        aggregateKey,
        field,
        group,
        labelField: optionLabelField,
        relation,
        label,
      }),
    [aggregateKey, field, group, label, optionLabelField, relation],
  );

  return React.useMemo(
    () => ({
      filters,
      filterFields,
      ...(groupOption ? { groupOption } : {}),
    }),
    [filterFields, filters, groupOption],
  );
}

function relationFilterConfig(
  metadata: ModelRelationFilterMetadata | undefined,
  override: {
    field: string | undefined;
    mode: ModelRelationFilterMode | undefined;
  },
): ModelRelationFilterMetadata | undefined {
  if (!override.field) return metadata;
  return {
    field: override.field,
    mode: override.mode ?? metadata?.mode ?? "lookup",
    ...(metadata?.aggregateKey ? { aggregateKey: metadata.aggregateKey } : {}),
  };
}

function relationFacetFilter(
  filter: ModelRelationFilterMetadata,
  value: string,
): DataViewFilter {
  if (filter.mode === "id") return { [filter.field]: value };
  return { [filter.field]: { exact: value } };
}

function relationGroupOption({
  aggregateKey,
  field,
  group,
  label,
  labelField,
  relation,
}: {
  aggregateKey: string | undefined;
  field: string;
  group: DataViewGroup | false | undefined;
  label: React.ReactNode;
  labelField: string | undefined;
  relation: RelationFieldInfo | null;
}): DataToolbarGroupOption | undefined {
  if (!relation || group === false) return undefined;
  const resolvedGroup = group;
  if (!resolvedGroup && !aggregateKey) return undefined;
  const defaultGroup = {
    field: `${field}.${labelField ?? relation.labelField}`,
    aggregateField: field,
    aggregateKey: aggregateKey ?? field,
  };
  const optionGroup = resolvedGroup ?? defaultGroup;
  return {
    id: optionGroup.field,
    label,
    group: optionGroup,
  };
}

function relationLabel(field: string): string {
  return field.charAt(0).toUpperCase() + field.slice(1);
}
