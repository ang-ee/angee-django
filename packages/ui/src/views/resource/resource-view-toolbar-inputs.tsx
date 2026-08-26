import * as React from "react";
import type { ModelMetadata, Row } from "@angee/metadata";

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
  mergeGroupOptions,
  textFilterValue,
} from "./resource-view-utils";

export interface UseResourceViewToolbarInputsProps<TRow extends Row> {
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
  explicitGroupOptionsReplaceInferred?: boolean;
  filterOptions?: readonly ResourceToolbarFilterOption[];
  contributedFilterOptions?: readonly ResourceToolbarFilterOption[];
  customFilterFields?: readonly ResourceToolbarFilterField[];
  contributedCustomFilterFields?: readonly ResourceToolbarFilterField[];
  textFilterField?: string;
  groupStack?: readonly ResourceViewGroup[];
}

export interface ResourceViewToolbarInputState {
  pager: PagerState;
  groupOptions: readonly ResourceToolbarGroupOption[];
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
  groupOptions,
  contributedGroupOptions = [],
  explicitGroupOptionsReplaceInferred = false,
  filterOptions: explicitFilterOptions,
  contributedFilterOptions = [],
  customFilterFields: explicitCustomFilterFields,
  contributedCustomFilterFields = [],
  textFilterField,
  groupStack = resourceView.state.groupStack,
}: UseResourceViewToolbarInputsProps<TRow>): ResourceViewToolbarInputState {
  const pager = React.useMemo<PagerState>(() => ({
    total: list.total,
    page: list.page,
    pageSize: list.pageSize,
    hasPrev: list.hasPrev,
    hasNext: list.hasNext,
  }), [list.hasNext, list.hasPrev, list.page, list.pageSize, list.total]);
  const toolbarDefaultGroups = React.useMemo(
    () => defaultGroupsForToolbar(defaultGroup, defaultGroups),
    [defaultGroup, defaultGroups],
  );
  const inferredGroups = React.useMemo(
    () => buildGroupOptions(columns, modelMetadata, toolbarDefaultGroups),
    [columns, modelMetadata, toolbarDefaultGroups],
  );
  const mergedContributedGroups = React.useMemo(
    () => mergeGroupOptions(groupOptions, contributedGroupOptions),
    [contributedGroupOptions, groupOptions],
  );
  const resolvedGroupOptions = React.useMemo(
    () => explicitGroupOptionsReplaceInferred && groupOptions !== undefined
      ? groupOptions
      : mergeGroupOptions(mergedContributedGroups, inferredGroups),
    [
      explicitGroupOptionsReplaceInferred,
      groupOptions,
      inferredGroups,
      mergedContributedGroups,
    ],
  );
  const inferredCustomFilterFields = React.useMemo(
    () => buildFilterFields(columns, rows, modelMetadata),
    [columns, modelMetadata, rows],
  );
  const inferredFilterOptions = React.useMemo(
    () => buildFilterOptions(columns, rows, inferredCustomFilterFields),
    [columns, inferredCustomFilterFields, rows],
  );
  const explicitAndContributedFilters = React.useMemo(
    () => mergeFilterOptions(explicitFilterOptions, contributedFilterOptions),
    [contributedFilterOptions, explicitFilterOptions],
  );
  const resolvedFilterOptions = React.useMemo(
    () => mergeFilterOptions(explicitAndContributedFilters, inferredFilterOptions),
    [explicitAndContributedFilters, inferredFilterOptions],
  );
  const explicitAndContributedFields = React.useMemo(
    () => mergeFilterFields(
      explicitCustomFilterFields,
      contributedCustomFilterFields,
    ),
    [contributedCustomFilterFields, explicitCustomFilterFields],
  );
  const resolvedCustomFilterFields = React.useMemo(
    () => mergeFilterFields(
      explicitAndContributedFields,
      inferredCustomFilterFields,
    ),
    [explicitAndContributedFields, inferredCustomFilterFields],
  );
  const activeFilterIds = React.useMemo(
    () => activeFilterIdsFor(resourceView.state.filter, resolvedFilterOptions),
    [resourceView.state.filter, resolvedFilterOptions],
  );
  const customFilterChips = React.useMemo(
    () => customFilterChipsFor(
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
  const filterText = textFilterValue(resourceView.state.filter, textFilterField);
  return {
    pager,
    groupOptions: resolvedGroupOptions,
    groupingEnabled: resolvedGroupOptions.length > 0 || groupStack.length > 0,
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
