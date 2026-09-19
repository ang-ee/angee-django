import * as React from "react";
import { isClientRowModel, useSchemaFieldMetadata, type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { queryForColumns } from "./resource-query";

import type {
  ResourceToolbarCustomFilterChip,
  ResourceToolbarFilterField,
  ResourceToolbarFilterOption,
  ResourceToolbarGroupOption,
} from "../../toolbars";
import type { PagerState } from "../../ui/pager";
import type { ResourceViewContextValue } from "./resource-view-context";
import {
  RESOURCE_VIEW_KINDS,
  type ResourceViewDefaultGroups,
  type ResourceViewGroup,
  type ResourceViewKind,
} from "./resource-view-model";
import type { ColumnDescriptor } from "../page";
import {
  activeFilterIdsFor,
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  customFilterChipsFor,
  mergeFilterFields,
  mergeFilterOptions,
  textFilterValue,
} from "./resource-view-utils";
import { relationFilterFields } from "../relation/relation-filter";

export interface UseResourceViewToolbarInputsProps<TRow extends Row> {
  query?: ResourceQuery;
  /** Disable inferred shortcuts and row-sampled values for server projections. */
  inferOptions?: boolean;
  /** Authored collections declare their server boundary without model metadata. */
  serverGrouping?: boolean;
  columns: readonly ColumnDescriptor<TRow>[];
  rows: readonly TRow[];
  modelMetadata: ModelMetadata | null;
  resourceView: ResourceViewContextValue;
  list: {
    total: number | undefined;
    page: number;
    pageSize: number;
    hasPrev: boolean;
    hasNext: boolean;
  };
  defaultGroup?: ResourceViewGroup | null;
  defaultGroups?: ResourceViewDefaultGroups;
  groupOptions?: readonly ResourceToolbarGroupOption[];
  contributedGroupOptions?: readonly ResourceToolbarGroupOption[];
  filterOptions?: readonly ResourceToolbarFilterOption[];
  contributedFilterOptions?: readonly ResourceToolbarFilterOption[];
  customFilterFields?: readonly ResourceToolbarFilterField[];
  contributedCustomFilterFields?: readonly ResourceToolbarFilterField[];
  textFilterField?: string | null;
  groupStack?: readonly ResourceViewGroup[];
}

export interface ResourceViewToolbarInputState {
  pager: PagerState;
  groupOptions: readonly ResourceToolbarGroupOption[];
  customGroupOptions: readonly ResourceToolbarGroupOption[];
  groupingEnabled: boolean;
  filterOptions: readonly ResourceToolbarFilterOption[];
  customFilterFields: readonly ResourceToolbarFilterField[];
  customFilterChips: readonly ResourceToolbarCustomFilterChip[];
  activeFilterIds: readonly string[];
  filterText: string;
}

/** Derive the toolbar's pager/group/filter inputs from one list surface. */
export function useResourceViewToolbarInputs<TRow extends Row>({
  columns,
  rows,
  modelMetadata,
  resourceView,
  list,
  defaultGroup,
  defaultGroups,
  query,
  inferOptions = true,
  serverGrouping: explicitServerGrouping,
  groupOptions,
  contributedGroupOptions = [],
  filterOptions: explicitFilterOptions,
  contributedFilterOptions = [],
  customFilterFields: explicitCustomFilterFields,
  contributedCustomFilterFields = [],
  textFilterField,
  groupStack = resourceView.state.groupStack,
}: UseResourceViewToolbarInputsProps<TRow>): ResourceViewToolbarInputState {
  const schemaMetadata = useSchemaFieldMetadata();
  const pager = React.useMemo<PagerState>(
    () => ({
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      hasPrev: list.hasPrev,
      hasNext: list.hasNext,
    }),
    [list.hasNext, list.hasPrev, list.page, list.pageSize, list.total],
  );
  const toolbarDefaultGroups = React.useMemo(
    () => defaultGroupsForToolbar(defaultGroup, defaultGroups),
    [defaultGroup, defaultGroups],
  );
  const resourceQuery = React.useMemo(
    () =>
      query ?? queryForColumns(columns, modelMetadata, toolbarDefaultGroups),
    [query, columns, modelMetadata, toolbarDefaultGroups],
  );
  const serverGrouping = explicitServerGrouping ?? Boolean(
    modelMetadata &&
      !isClientRowModel(modelMetadata.resource) &&
      (resourceView.state.view === "list" ||
        resourceView.state.view === "board"),
  );
  const customGroupOptions = React.useMemo(
    () => buildGroupOptions(columns, modelMetadata, toolbarDefaultGroups, resourceQuery)
    .filter(({ group }) => {
      const axis = resourceQuery.axes[group.field];
      return serverGrouping
        ? Boolean(axis?.server)
        : Boolean(axis?.identityPath);
    }),
    [columns, modelMetadata, toolbarDefaultGroups, resourceQuery, serverGrouping],
  );
  const resolvedGroupOptions = React.useMemo(() => {
    const presets = groupOptions ?? (contributedGroupOptions.length
      ? contributedGroupOptions
      : inferOptions ? customGroupOptions : []);
    return presets.filter(({ group }) => {
      const supported = customGroupOptions.find(
        (option) => option.group.field === group.field,
      );
      if (!supported) return false;
      return group.granularity === undefined
        || supported.granularities?.includes(group.granularity) === true;
    });
  }, [groupOptions, contributedGroupOptions, inferOptions, customGroupOptions]);
  const inferredCustomFilterFields = React.useMemo(
    () =>
      buildFilterFields(columns, inferOptions ? rows : [], modelMetadata, resourceQuery),
    [inferOptions, columns, modelMetadata, rows, resourceQuery],
  );
  const relationCustomFilterFields = React.useMemo(
    () => relationFilterFields(resourceQuery, modelMetadata, schemaMetadata),
    [modelMetadata, resourceQuery, schemaMetadata],
  );
  const inferredFilterOptions = React.useMemo(
    () =>
      inferOptions
        ? buildFilterOptions(columns, rows, inferredCustomFilterFields)
        : [],
    [inferOptions, columns, inferredCustomFilterFields, rows],
  );
  const explicitAndContributedFilters = React.useMemo(
    () => mergeFilterOptions(explicitFilterOptions, contributedFilterOptions),
    [contributedFilterOptions, explicitFilterOptions],
  );
  const resolvedFilterOptions = React.useMemo(
    () =>
      mergeFilterOptions(explicitAndContributedFilters, inferredFilterOptions),
    [explicitAndContributedFilters, inferredFilterOptions],
  );
  const explicitAndContributedFields = React.useMemo(
    () =>
      mergeFilterFields(
        explicitCustomFilterFields,
        mergeFilterFields(
          contributedCustomFilterFields,
          relationCustomFilterFields,
        ),
      ),
    [contributedCustomFilterFields, explicitCustomFilterFields, relationCustomFilterFields],
  );
  const resolvedCustomFilterFields = React.useMemo(
    () =>
      mergeFilterFields(
        explicitAndContributedFields,
        inferredCustomFilterFields,
      ).filter((field) => Boolean(resourceQuery.fields[field.field ?? field.id]?.filter?.operators.length)),
    [explicitAndContributedFields, inferredCustomFilterFields, resourceQuery],
  );
  const activeFilterIds = React.useMemo(
    () => activeFilterIdsFor(resourceView.state.filter, resolvedFilterOptions),
    [resourceView.state.filter, resolvedFilterOptions],
  );
  const customFilterChips = React.useMemo(
    () =>
      customFilterChipsFor(
        resourceView.state.filter,
        resolvedFilterOptions,
        resolvedCustomFilterFields,
        textFilterField,
      ),
    [
      resourceView.state.filter,
      resolvedCustomFilterFields,
      resolvedFilterOptions,
      textFilterField,
    ],
  );
  const filterText = textFilterValue(
    resourceView.state.filter,
    textFilterField,
  );
  return {
    pager,
    groupOptions: resolvedGroupOptions,
    customGroupOptions,
    groupingEnabled: customGroupOptions.length > 0 || groupStack.length > 0,
    filterOptions: resolvedFilterOptions,
    customFilterFields: resolvedCustomFilterFields,
    customFilterChips,
    activeFilterIds,
    filterText,
  };
}

export function defaultGroupForView(
  defaultGroup: ResourceViewGroup | null | undefined,
  defaultGroups: ResourceViewDefaultGroups | undefined,
  view: ResourceViewKind,
): ResourceViewGroup | null {
  if (defaultGroups && Object.prototype.hasOwnProperty.call(defaultGroups, view)) {
    return defaultGroups[view] ?? null;
  }
  return defaultGroup ?? null;
}

export function defaultGroupsForToolbar(
  defaultGroup: ResourceViewGroup | null | undefined,
  defaultGroups: ResourceViewDefaultGroups | undefined,
): readonly ResourceViewGroup[] {
  const groups: ResourceViewGroup[] = [];
  if (defaultGroup) groups.push(defaultGroup);
  for (const view of RESOURCE_VIEW_KINDS) {
    const group = defaultGroups?.[view];
    if (group) groups.push(group);
  }
  return groups;
}
