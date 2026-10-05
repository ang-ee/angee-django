import * as React from "react";
import { Filter, type GroupSpec } from "@angee/metadata";
import type { FilterClause } from "../../../toolbars/FilterClauseEditor";
import type { ResourceViewContextValue } from "../resource-view-context";
import { addCustomFilter, removeCustomFilter } from "../utils/filter-mutations";
import { activeItems } from "./active";
import { useUiT } from "../../../i18n";
import type { ResourceSearch, SearchCatalog } from "./types";

export interface UseResourceSearchInput {
  resourceView: ResourceViewContextValue;
  catalog: SearchCatalog;
  groupStack?: readonly GroupSpec[];
  groupingEnabled?: boolean;
  maxGroupDepth?: number;
}

/** Commands change provider state; this model stores no parallel query state. */
export function useResourceSearch({ resourceView, catalog, groupStack = resourceView.state.groupStack,
  groupingEnabled = catalog.groups.length > 0 || groupStack.length > 0, maxGroupDepth,
}: UseResourceSearchInput): ResourceSearch {
  const t = useUiT();
  return React.useMemo<ResourceSearch>(() => {
    const active = activeItems({ ...resourceView.state, groupStack }, catalog, t);
    const setGroupStack = (stack: readonly GroupSpec[]) => {
      if (groupingEnabled) resourceView.setGroupStack(maxGroupDepth === undefined ? stack : stack.slice(-Math.max(1, maxGroupDepth)));
    };
    const toggleFilter = (id: string) => {
      const option = catalog.filters.find((candidate) => candidate.id === id);
      if (!option) return;
      const facet = Filter.facetFromFilter(option.filter);
      resourceView.setFilter((current) => facet
        ? Filter.from(current).toggleFacet(facet)
        : Filter.from(current).togglePreset(option.filter));
    };
    const setFacet = (field: string, ids: readonly string[]) => {
      const facet = catalog.facets.find((candidate) => candidate.field === field);
      if (!facet) return;
      const currentItem = active.find((item) => item.kind === "facet" && item.field === field);
      const options = [...facet.options, ...(currentItem?.kind === "facet" ? currentItem.options.filter((option) => !facet.options.some((known) => known.id === option.id)) : [])];
      const selected = options.filter((option) => ids.includes(option.id));
      const blank = selected.find((option) => option.value === undefined);
      resourceView.setFilter((current) => {
        const filter = Filter.from(current);
        const remaining = filter.withoutFields([field]);
        // A newly selected blank replaces values. Adding a value to an active
        // blank replaces that blank, including native multi-select proposals.
        if (blank && !(filter.hasPreset(blank.filter) && selected.some((option) => option.value !== undefined))) {
          return Filter.combine(remaining, blank.filter);
        }
        return selected.reduce((next, option) => {
          const value = Filter.facetFromFilter(option.filter);
          return value ? Filter.from(next).toggleFacet(value) : next;
        }, remaining);
      });
    };
    const toggleFacetOption: ResourceSearch["toggleFacetOption"] = (field, id, supplied) => {
      const facet = catalog.facets.find((facet) => facet.field === field);
      const option = facet?.options.find((candidate) => candidate.id === id) ?? supplied;
      if (!facet) return;
      if (!option) return;
      resourceView.setFilter((current) => {
        const filter = Filter.from(current);
        const valued = Filter.facetFromFilter(option.filter);
        if (!valued) return filter.hasPreset(option.filter) ? filter.withoutFields([field])
          : Filter.combine(filter.withoutFields([field]), option.filter);
        const blank = catalog.facets.find((facet) => facet.field === field)?.options.find((candidate) => candidate.value === undefined);
        const base = blank && filter.hasPreset(blank.filter) ? Filter.from(filter.withoutFields([field])) : filter;
        return base.toggleFacet(valued);
      });
    };
    const setText = (value: string, field = catalog.text[0]?.field) => {
      if (field) resourceView.setFilter((current) => Filter.from(current).withTextTerm(value, field));
    };
    const setClause = (field: string, clause: FilterClause | null) => resourceView.setFilter((current) => {
      const remaining = Filter.from(current).withoutFields([field]);
      return clause ? addCustomFilter(remaining, { ...clause, field }) : remaining;
    });
    const applyFavorite = (id: string) => {
      const favorite = catalog.favorites.find((candidate) => candidate.id === id);
      if (favorite) resourceView.applyFavorite(favorite);
    };
    const toggleFavorite = (id: string) => {
      if (!active.some((item) => item.kind === "favorite" && item.id === `favorite:${id}`)) applyFavorite(id);
      else if (id === resourceView.state.preset) resourceView.clearPreset();
      else resourceView.resetQuery();
    };
    const removeGroup = (index: number) => setGroupStack(groupStack.filter((_, position) => position !== index));
    return {
      catalog, active, groupStack, groupingEnabled, maxGroupDepth, queryDirty: resourceView.queryDirty,
      filter: resourceView.state.filter, setFilter: resourceView.setFilter,
      setText, toggleFilter, setFacet, toggleFacetOption,
      addClause: (clause) => resourceView.setFilter((current) => addCustomFilter(current, clause)),
      setClause,
      addGroup: (level) => setGroupStack([...groupStack, level]),
      removeGroup,
      moveGroup: (from, to) => {
        if (from < 0 || from >= groupStack.length || to < 0 || to >= groupStack.length) return;
        const stack = [...groupStack];
        const [level] = stack.splice(from, 1);
        stack.splice(to, 0, level!);
        setGroupStack(stack);
      },
      setGroupLevel: (index, level) => setGroupStack(groupStack.map((current, position) => position === index ? level : current)),
      setGroupStack,
      applyFavorite, toggleFavorite,
      saveFavorite: resourceView.saveFavorite,
      renameFavorite: resourceView.renameFavorite,
      pinFavorite: resourceView.pinFavorite,
      clear: (id) => {
        const item = active.find((candidate) => candidate.id === id);
        if (!item) return;
        switch (item.kind) {
          case "text": setText("", item.field); break;
          case "filter": toggleFilter(id.slice("filter:".length)); break;
          case "facet": setFacet(item.field, []); break;
          case "clause": resourceView.setFilter((current) => removeCustomFilter(current, id.slice("clause:".length))); break;
          case "group": removeGroup(item.index); break;
          case "favorite": toggleFavorite(id.slice("favorite:".length)); break;
        }
      },
      resetQuery: resourceView.resetQuery,
      clearQuery: resourceView.clearQuery,
    };
  }, [resourceView, catalog, groupStack, groupingEnabled, maxGroupDepth, t]);
}
