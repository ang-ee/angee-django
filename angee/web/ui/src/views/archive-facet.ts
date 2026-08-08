import * as React from "react";
import type { ModelMetadata } from "@angee/metadata";
import { archiveFilterField } from "@angee/metadata";

import type { ResourceViewContextValue } from "./resource-view-context";
import {
  Filter,
  type ResourceViewFilter,
} from "./resource-view-model";

/**
 * The archive facet — the three read scopes over a model's `ArchiveMixin` flag
 * (the field resource metadata marks `archivable`).
 *
 * "active" is the default and is deliberately *absent* from the URL-owned view
 * filter: collection surfaces scope to unarchived rows by injecting the default
 * at query-merge time ({@link combineWithArchiveDefault}), so clearing filters
 * returns to the default and a favorite carries only explicit choices.
 * "archived" pins the flag true; "all" pins the explicit both-values list so
 * the URL distinguishes it from the default. The injected default is an
 * ordinary `where` entry — visible in the GraphQL request — never a hidden
 * server-side scope.
 */
export const ARCHIVE_FACET_VALUES = ["active", "archived", "all"] as const;
export type ArchiveFacetValue = (typeof ARCHIVE_FACET_VALUES)[number];

/** The toolbar payload for the archive chip — its scope plus the setter. */
export interface ArchiveFacetToolbarProps {
  value: ArchiveFacetValue;
  onValueChange: (value: ArchiveFacetValue) => void;
}

/** Read the facet scope from the view filter; an unmentioned field reads "active". */
export function archiveFacetValue(
  filter: ResourceViewFilter,
  field: string,
): ArchiveFacetValue {
  const value = filter[field];
  if (value === undefined) return "active";
  if (value === true) return "archived";
  if (value === false) return "active";
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const exact = (value as Record<string, unknown>).exact;
    if (exact === true) return "archived";
    if (exact === false) return "active";
  }
  return "all";
}

/** Write the facet scope into the view filter: "active" clears the field back
 * to the default; "archived" pins the flag; "all" pins the both-values list. */
export function withArchiveFacetValue(
  filter: ResourceViewFilter,
  field: string,
  value: ArchiveFacetValue,
): ResourceViewFilter {
  const next = { ...filter };
  if (value === "active") delete next[field];
  else if (value === "archived") next[field] = { exact: true };
  else next[field] = { inList: [false, true] };
  return next;
}

/**
 * Merge a surface's base filter with the URL-owned view filter, then scope to
 * unarchived rows when the model is archivable and neither filter mentions the
 * flag. Every collection merge site (list, grouped, client, pivot — and through
 * the merged result, facet counts) applies this one rule, so the scopes agree.
 */
export function combineWithArchiveDefault(
  baseFilter: unknown,
  stateFilter: unknown,
  metadata: ModelMetadata | null,
): ResourceViewFilter | undefined {
  const merged = Filter.combineOptional(baseFilter, stateFilter);
  const field = archiveFilterField(metadata);
  if (!field) return merged;
  if (merged && Filter.from(merged).mentionsField(field)) return merged;
  return Filter.combine(merged ?? {}, { [field]: { exact: false } });
}

/** The archive chip's toolbar payload, or `undefined` for a non-archivable model. */
export function useArchiveFacetToolbar(
  resourceView: ResourceViewContextValue,
  metadata: ModelMetadata | null,
): ArchiveFacetToolbarProps | undefined {
  const field = archiveFilterField(metadata);
  const { setFilter } = resourceView;
  const filter = resourceView.state.filter;
  return React.useMemo(() => {
    if (!field) return undefined;
    return {
      value: archiveFacetValue(filter, field),
      onValueChange: (value) =>
        setFilter(withArchiveFacetValue(filter, field, value)),
    };
  }, [field, filter, setFilter]);
}
