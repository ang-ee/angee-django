import * as React from "react";
import { Filter, isClientRowModel, useSchemaFieldMetadata, type ModelMetadata, type ResourceQuery, type Row } from "@angee/metadata";
import type { FilterClauseField, ResourceToolbarFilterOption, ResourceToolbarGroupOption } from "../../../toolbars";
import type { ColumnDescriptor } from "../../page";
import { relationFilterFields } from "../../relation/relation-filter";
import { fieldLabel, relationFieldInfo, relationFieldInfoForQueryField } from "../model-metadata-defaults";
import { queryForColumns } from "../resource-query";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewDefaultGroups, ResourceViewGroup } from "../resource-view-model";
import { buildFilterFields, buildFilterOptions, buildGroupOptions, mergeFilterFields, mergeFilterOptions } from "../resource-view-utils";
import { defaultGroupsForToolbar } from "./group-defaults";
import type { SearchCatalog, SearchFacet } from "./types";

export interface UseSearchCatalogInput<TRow extends Row> {
  query?: ResourceQuery;
  inferOptions?: boolean;
  serverGrouping?: boolean;
  columns: readonly ColumnDescriptor<TRow>[];
  rows: readonly TRow[];
  modelMetadata: ModelMetadata | null;
  resourceView: ResourceViewContextValue;
  defaultGroup?: ResourceViewGroup | null;
  defaultGroups?: ResourceViewDefaultGroups;
  groupOptions?: readonly ResourceToolbarGroupOption[];
  contributedGroupOptions?: readonly ResourceToolbarGroupOption[];
  filterOptions?: readonly ResourceToolbarFilterOption[];
  contributedFilterOptions?: readonly ResourceToolbarFilterOption[];
  customFilterFields?: readonly FilterClauseField[];
  contributedCustomFilterFields?: readonly FilterClauseField[];
  declaredFacets?: readonly SearchFacet[];
  scalarFacets?: readonly SearchFacet[];
  textFilterField?: string | null;
  /** Additional fields requested by text presentations. */
  textShortcutFields?: readonly string[];
}

const EMPTY = [] as const;

/** Resolve declaration > contribution > inference once for every collection kind. */
export function useSearchCatalog<TRow extends Row>(input: UseSearchCatalogInput<TRow>): SearchCatalog {
  const schema = useSchemaFieldMetadata();
  const { columns, rows, modelMetadata, resourceView, inferOptions = true } = input;
  const defaults = React.useMemo(() => defaultGroupsForToolbar(input.defaultGroup, input.defaultGroups), [input.defaultGroup, input.defaultGroups]);
  const query = React.useMemo(() => input.query ?? queryForColumns(columns, modelMetadata, defaults), [input.query, columns, modelMetadata, defaults]);
  const serverGrouping = input.serverGrouping ?? Boolean(modelMetadata && !isClientRowModel(modelMetadata.resource)
    && (resourceView.state.view === "list" || resourceView.state.view === "board"));
  const groups = React.useMemo(() => buildGroupOptions(columns, modelMetadata, defaults, query).filter(({ group }) => {
    const axis = query.axes[group.field];
    return serverGrouping ? Boolean(axis?.server) : Boolean(axis?.identityPath);
  }), [columns, modelMetadata, defaults, query, serverGrouping]);
  const inferredFields = React.useMemo(() => buildFilterFields(columns, inferOptions ? rows : EMPTY, modelMetadata, query), [columns, inferOptions, rows, modelMetadata, query]);
  const relations = React.useMemo(() => relationFilterFields(query, modelMetadata, schema), [query, modelMetadata, schema]);
  const contributedFacets = React.useMemo(() => {
    const seen = new Set<string>();
    return [...(input.declaredFacets ?? EMPTY), ...(input.scalarFacets ?? EMPTY)].filter((facet) => {
      if (seen.has(facet.field)) return false;
      seen.add(facet.field);
      return true;
    });
  }, [input.declaredFacets, input.scalarFacets]);
  const fields = React.useMemo(() => mergeFilterFields(input.customFilterFields,
    mergeFilterFields(input.contributedCustomFilterFields,
      mergeFilterFields(contributedFacets.filter((facet) => facet.source === "relation" || facet.options.length > 0).map(facetField), mergeFilterFields(relations, inferredFields))))
    .filter((field) => Boolean(query.fields[field.field ?? field.id]?.filter?.operators.length)),
  [input.customFilterFields, input.contributedCustomFilterFields, contributedFacets, relations, inferredFields, query]);
  const filters = React.useMemo(() => mergeFilterOptions(input.filterOptions, input.contributedFilterOptions ?? EMPTY, query), [input.filterOptions, input.contributedFilterOptions, query]);
  const facets = React.useMemo(() => {
    const choices = buildFilterOptions(columns, inferOptions ? rows : EMPTY, fields);
    const predicates = new Set(filters.map((option) => JSON.stringify(query.toWhere(option.filter))));
    return fields.flatMap((field): SearchFacet[] => {
      const name = field.field ?? field.id;
      const contributed = contributedFacets.find((facet) => facet.field === name);
      const declared = input.customFilterFields?.find((candidate) => (candidate.field ?? candidate.id) === name);
      const inferred = choices.flatMap((option) => {
        const value = Filter.facetFromFilter(option.filter);
        return value?.field === name ? [{ id: option.id, label: option.label, filter: option.filter, value: value.value }] : [];
      });
      if (!contributed && !(declared?.options?.length) && (!inferOptions || !inferred.length)) return [];
      // Authored labels/options win. Server buckets retain their identity and
      // drill predicate, including blanks absent from a clause's options.
      const options = [...(contributed?.options ?? EMPTY)];
      const authored = declared?.options ? inferred.map((option) => ({
        ...(options.find((bucket) => bucket.value === option.value) ?? option), label: option.label,
      })) : undefined;
      const merged = mergeFilterOptions(authored, [...options, ...inferred], query);
      const relation = contributed?.source === "relation"
        ? modelMetadata ? relationFieldInfo(name, modelMetadata, schema) : relationFieldInfoForQueryField(query.fields[name], schema)
        : null;
      const axis = relation && query.axes[name]?.server && query.axes[name]?.drill ? query.axis(name) : null;
      return [{ field: name, label: field.label ?? contributed?.label ?? fieldLabel(name, undefined),
        source: declared?.options ? "declared" : contributed?.source ?? "scalar",
        ...(contributed?.group !== undefined ? { group: contributed.group } : {}),
        ...(relation && axis ? { relation, optionForValue: (value: string, label: React.ReactNode = value) => {
          const bucket = { key: { [axis.groupBy().valueKey]: value } };
          const filter = axis.drill(bucket);
          return filter ? { id: axis.bucketId(bucket), label, filter, value } : undefined;
        } } : {}),
        options: merged.flatMap((option) => {
          const key = JSON.stringify(query.toWhere(option.filter));
          if (predicates.has(key)) return [];
          predicates.add(key);
          const facet = Filter.facetFromFilter(option.filter);
          return [{ id: option.id, label: option.label, filter: option.filter, ...(facet ? { value: facet.value } : {}) }];
        }),
      }];
    });
  }, [columns, inferOptions, rows, fields, filters, contributedFacets, input.customFilterFields, query, modelMetadata, schema]);
  const curatedGroups = React.useMemo(() => {
    const contributed = input.contributedGroupOptions ?? contributedFacets.flatMap((facet) => facet.source === "relation" && facet.group !== false
      ? [{ id: facet.field, label: facet.label, group: facet.group ?? { field: facet.field } }] : []);
    return (input.groupOptions ?? (contributed.length ? contributed : inferOptions ? groups : EMPTY)).filter(({ group }) => {
      const supported = groups.find((option) => option.group.field === group.field);
      return supported && (group.granularity === undefined || supported.granularities?.includes(group.granularity));
    });
  }, [input.groupOptions, input.contributedGroupOptions, contributedFacets, inferOptions, groups]);
  const text = React.useMemo(() => searchTextFields(query, input.textFilterField, input.textShortcutFields).map((field) => ({
    field, label: fieldLabel(field, modelMetadata?.fields[field], columns.find((column) => column.field === field)?.header),
  })), [query, input.textFilterField, input.textShortcutFields, modelMetadata, columns]);
  return React.useMemo(() => ({ text, filters, facets, fields, groups, curatedGroups, favorites: resourceView.savedFavorites }),
    [text, filters, facets, fields, groups, curatedGroups, resourceView.savedFavorites]);
}

/** Null removes only the default; explicitly requested text fields still apply. */
export function searchTextFields(query: ResourceQuery, field?: string | null, shortcuts: readonly string[] = EMPTY): readonly string[] {
  const first = field === null ? undefined : field ?? query.textSearchFields()[0];
  return [...new Set([...(first ? [first] : EMPTY), ...shortcuts])].filter((name) => query.fields[name]?.filter?.operators.includes("iContains"));
}

function facetField(facet: SearchFacet): FilterClauseField {
  return { id: facet.field, field: facet.field, label: facet.label, type: "selection",
    options: facet.options.flatMap((option) => option.value === undefined ? [] : [{ value: option.value, label: option.label }]),
  };
}
