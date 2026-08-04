import * as React from "react";

import type { ResourceToolbarProps } from "../toolbars";
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
    | "onColumnStackChange"
    | "onPageChange"
    | "onPageSizeChange"
    | "onViewChange"
  > {
  resourceView: ResourceViewContextValue;
  view?: ResourceViewKind;
  group?: ResourceViewGroup | null;
  groupStack?: readonly ResourceViewGroup[];
  groupingEnabled?: boolean;
  /** Offer the column-axis picker (the pivot kind's second axis). */
  columnAxisEnabled?: boolean;
  textFilterField?: string;
}

/** Shared toolbar command wiring for resource-backed and in-memory row lists. */
export function useResourceToolbarProps({
  resourceView,
  textFilterField,
  view,
  group,
  groupStack,
  columnStack,
  groupOptions,
  groupingEnabled = true,
  columnAxisEnabled = false,
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
      columnStack: columnAxisEnabled
        ? (columnStack ?? resourceView.state.columnStack)
        : undefined,
      onColumnStackChange: columnAxisEnabled
        ? resourceView.setColumnStack
        : undefined,
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
      onFilterToggle: (id) =>
        resourceView.setFilter(
          nextFacetFilter(resourceView.state.filter, filterOptions, id),
        ),
      onFilterTextChange: (value) =>
        resourceView.setFilter(
          nextTextFilter(resourceView.state.filter, value, textFilterField),
        ),
    }),
    [
      columnAxisEnabled,
      filterOptions,
      group,
      groupStack,
      columnStack,
      groupOptions,
      groupingEnabled,
      props,
      resourceView.applyFavorite,
      resourceView.saveFavorite,
      resourceView.setColumnStack,
      resourceView.setFilter,
      resourceView.setGroupStack,
      resourceView.setPageSize,
      resourceView.setView,
      resourceView.state.columnStack,
      resourceView.state.filter,
      setPage,
      textFilterField,
      view,
    ],
  );
}
