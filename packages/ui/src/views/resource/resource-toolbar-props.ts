import * as React from "react";

import type { ResourceToolbarProps } from "../../toolbars";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ResourceViewGroup, ResourceViewKind } from "./resource-view-model";
import {
  addCustomFilter as addCustomFilterToFilter,
  nextFacetFilter,
  nextTextFilter,
  removeCustomFilter,
} from "./resource-view-utils";

export interface UseResourceToolbarPropsInput
  extends Omit<
    ResourceToolbarProps,
    | "onClearGroup"
    | "onCustomFilterAdd"
    | "onCustomFilterRemove"
    | "onFavoriteSave"
    | "onFavoriteSelect"
    | "onFilterTextChange"
    | "onFilterToggle"
    | "onGroupStackChange"
    | "onPageChange"
    | "onPageSizeChange"
    | "onViewChange"
  > {
  resourceView: ResourceViewContextValue;
  view?: ResourceViewKind;
  group?: ResourceViewGroup | null;
  groupStack?: readonly ResourceViewGroup[];
  groupingEnabled?: boolean;
  textFilterField?: string | null;
}

/** Shared toolbar command wiring for resource-backed and in-memory row lists. */
export function useResourceToolbarProps({
  resourceView,
  textFilterField,
  view,
  group,
  groupStack,
  groupOptions,
  groupingEnabled = true,
  filterOptions = [],
  ...props
}: UseResourceToolbarPropsInput): ResourceToolbarProps {
  const setPage = React.useCallback(
    (page: number) => {
      resourceView.setPage(page);
    },
    [resourceView.setPage],
  );

  return React.useMemo<ResourceToolbarProps>(
    () => ({
      ...props,
      view,
      group: groupingEnabled ? group : undefined,
      groupStack: groupingEnabled ? groupStack : undefined,
      groupOptions: groupingEnabled ? groupOptions : undefined,
      filterOptions,
      onClearGroup: groupingEnabled
        ? () => resourceView.setGroupStack([])
        : undefined,
      onGroupStackChange: groupingEnabled ? resourceView.setGroupStack : undefined,
      onPageChange: setPage,
      onPageSizeChange: resourceView.setPageSize,
      onViewChange: view ? resourceView.setView : undefined,
      onCustomFilterAdd: (customFilter) =>
        resourceView.setFilter(
          addCustomFilterToFilter(resourceView.state.filter, customFilter),
        ),
      onCustomFilterRemove: (id) =>
        resourceView.setFilter(removeCustomFilter(resourceView.state.filter, id)),
      onFavoriteSave: resourceView.saveFavorite,
      onFavoriteSelect: resourceView.applyFavorite,
      onQueryReset: resourceView.resetQuery,
      queryDirty:
        Object.keys(resourceView.state.filter).length > 0
        || resourceView.state.groupStack.length > 0
        || Boolean(resourceView.state.sorting?.length),
      onFilterToggle: (id) =>
        resourceView.setFilter(
          nextFacetFilter(resourceView.state.filter, filterOptions, id),
        ),
      onFilterTextChange: textFilterField === null ? undefined : (value) =>
        resourceView.setFilter(
          nextTextFilter(resourceView.state.filter, value, textFilterField),
        ),
    }),
    [
      filterOptions,
      group,
      groupStack,
      groupOptions,
      groupingEnabled,
      props,
      resourceView.applyFavorite,
      resourceView.saveFavorite,
      resourceView.resetQuery,
      resourceView.setFilter,
      resourceView.setGroupStack,
      resourceView.setPageSize,
      resourceView.setView,
      resourceView.state.filter,
      resourceView.state.groupStack,
      resourceView.state.sorting,
      setPage,
      textFilterField,
      view,
    ],
  );
}
