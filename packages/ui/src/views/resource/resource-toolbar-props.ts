import * as React from "react";
import { stableSerialize } from "@angee/refine";

import type { ResourceToolbarProps } from "../../toolbars";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ResourceViewGroup, ResourceViewKind } from "./resource-view-model";
import {
  activeFilterIdsFor,
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
  customGroupOptions,
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
    () => {
      const activeFavoriteIds = resourceView.savedFavorites.flatMap((favorite) =>
        stableSerialize(favorite.filter ?? {}) === stableSerialize(resourceView.state.filter)
          && (favorite.preset ?? undefined) === (resourceView.state.preset || undefined)
          ? [favorite.id] : []);
      return ({
      ...props,
      view,
      group: groupingEnabled ? group : undefined,
      groupStack: groupingEnabled ? groupStack : undefined,
      groupOptions: groupingEnabled ? groupOptions : undefined,
      customGroupOptions: groupingEnabled ? customGroupOptions : undefined,
      filterOptions,
      activeFavoriteIds,
      onClearGroup: groupingEnabled
        ? () => resourceView.setGroupStack([])
        : undefined,
      onGroupStackChange: groupingEnabled ? resourceView.setGroupStack : undefined,
      onPageChange: setPage,
      onPageSizeChange: resourceView.setPageSize,
      onViewChange: view && (props.availableViews?.length ?? 2) > 1 ? resourceView.setView : undefined,
      onCustomFilterAdd: (customFilter) =>
        resourceView.setFilter(
          addCustomFilterToFilter(resourceView.state.filter, customFilter),
        ),
      onCustomFilterRemove: (id) =>
        resourceView.setFilter(removeCustomFilter(resourceView.state.filter, id)),
      onFavoriteSave: resourceView.saveFavorite,
      onFavoriteSelect: resourceView.applyFavorite,
      onFavoriteRename: resourceView.renameFavorite,
      onFavoritePin: resourceView.pinFavorite,
      onFavoriteToggle: (favorite) => activeFavoriteIds.includes(favorite.id)
        ? favorite.id === resourceView.state.preset
          ? resourceView.clearPreset()
          : resourceView.resetQuery()
        : resourceView.applyFavorite(favorite),
      onQueryReset: resourceView.clearQuery,
      queryDirty: resourceView.queryDirty,
      onFilterToggle: (id) =>
        resourceView.setFilter(
          nextFacetFilter(resourceView.state.filter, filterOptions, id),
        ),
      onFacetChange: (field, optionId) => {
        const active = activeFilterIdsFor(resourceView.state.filter, filterOptions)
          .filter((id) => id.startsWith(`${field}:`));
        if (active.length === 1 && active[0] === optionId) return;
        const cleared = active.reduce(
          (filter, id) => nextFacetFilter(filter, filterOptions, id),
          resourceView.state.filter,
        );
        resourceView.setFilter(optionId ? nextFacetFilter(cleared, filterOptions, optionId) : cleared);
      },
      onFilterTextChange: textFilterField === null ? undefined : (value) =>
        resourceView.setFilter(
          nextTextFilter(resourceView.state.filter, value, textFilterField),
        ),
    });
    },
    [
      filterOptions,
      group,
      groupStack,
      groupOptions,
      customGroupOptions,
      groupingEnabled,
      props,
      resourceView.applyFavorite,
      resourceView.saveFavorite,
      resourceView.renameFavorite,
      resourceView.pinFavorite,
      resourceView.savedFavorites,
      resourceView.resetQuery,
      resourceView.clearQuery,
      resourceView.queryDirty,
      resourceView.clearPreset,
      resourceView.setFilter,
      resourceView.setGroupStack,
      resourceView.setPageSize,
      resourceView.setView,
      resourceView.state.filter,
      resourceView.state.preset,
      setPage,
      textFilterField,
      view,
    ],
  );
}
