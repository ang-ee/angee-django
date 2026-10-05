import type { ReactNode } from "react";
import type { GroupSpec, QueryFilter } from "@angee/metadata";
import type { FilterClause, FilterClauseField, FilterClauseOperator } from "../../../toolbars/FilterClauseEditor";
import type { ResourceToolbarFilterOption, ResourceToolbarGroupOption } from "../../../toolbars/ResourceToolbar";
import type { ResourceViewFavorite } from "../resource-view-model";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { RelationFieldInfo } from "../model-metadata-defaults";
import type { ComposedContainerChild } from "../../../runtime/containers";
import type { SearchShortcut } from "./shortcuts";

/** A bucket keeps its executable predicate, including the valueless blank bucket. */
export interface SearchFacetOption {
  id: string;
  label: ReactNode;
  filter: QueryFilter;
  value?: string;
}

export interface SearchFacet {
  field: string;
  label: ReactNode;
  options: readonly SearchFacetOption[];
  source: "relation" | "scalar" | "declared";
  /** Preserve an authored Facet's grouping opt-out or alternate group axis. */
  group?: GroupSpec | false;
  /** Query-resolved target and bucket factory for values beyond the facet page. */
  relation?: RelationFieldInfo;
  optionForValue?: (value: string, label?: ReactNode) => SearchFacetOption | undefined;
}

export interface SearchCatalog {
  /** The catalog owner's validated container projection, when it resolves shortcuts. */
  shortcuts?: readonly ComposedContainerChild<SearchShortcut>[];
  text: readonly { field: string; label: ReactNode }[];
  filters: readonly ResourceToolbarFilterOption[];
  facets: readonly SearchFacet[];
  fields: readonly FilterClauseField[];
  groups: readonly ResourceToolbarGroupOption[];
  curatedGroups: readonly ResourceToolbarGroupOption[];
  favorites: readonly ResourceViewFavorite[];
}

export type SearchActiveItem =
  | { id: `text:${string}`; kind: "text"; field: string; label: ReactNode; value: string }
  | { id: `filter:${string}`; kind: "filter"; label: ReactNode }
  | { id: `facet:${string}`; kind: "facet"; field: string; label: ReactNode; options: readonly SearchFacetOption[] }
  | { id: `clause:${string}:${FilterClauseOperator}`; kind: "clause"; label: ReactNode }
  | { id: `group:${number}`; kind: "group"; index: number; level: GroupSpec; label: ReactNode }
  | { id: `favorite:${string}`; kind: "favorite"; label: ReactNode };

/** A render-time projection and thin commands over the resource-view provider. */
export interface ResourceSearch {
  catalog: SearchCatalog;
  active: readonly SearchActiveItem[];
  groupStack: readonly GroupSpec[];
  groupingEnabled: boolean;
  maxGroupDepth?: number;
  queryDirty: boolean;
  filter: QueryFilter;
  setFilter: ResourceViewContextValue["setFilter"];
  setText(value: string, field?: string): void;
  toggleFilter(id: string): void;
  setFacet(field: string, optionIds: readonly string[]): void;
  toggleFacetOption(field: string, optionId: string, option?: SearchFacetOption): void;
  addClause(clause: FilterClause): void;
  setClause(field: string, clause: FilterClause | null): void;
  addGroup(level: GroupSpec): void;
  removeGroup(index: number): void;
  moveGroup(from: number, to: number): void;
  setGroupLevel(index: number, level: GroupSpec): void;
  setGroupStack(stack: readonly GroupSpec[]): void;
  applyFavorite(id: string): void;
  toggleFavorite(id: string): void;
  /** Absent when the provider has no writable preferences. */
  saveFavorite: ResourceViewContextValue["saveFavorite"];
  renameFavorite: ResourceViewContextValue["renameFavorite"];
  pinFavorite: ResourceViewContextValue["pinFavorite"];
  clear(itemId: SearchActiveItem["id"]): void;
  resetQuery(): void;
  clearQuery(): void;
}
