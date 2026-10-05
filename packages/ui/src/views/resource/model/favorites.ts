import * as v from "valibot";
import { GroupSpecsSchema, ResourceQuery, type ModelMetadata, type QueryFilter } from "@angee/metadata";
import type { VisibilityState } from "@tanstack/react-table";
import { RESOURCE_VIEW_SORT_DIRECTIONS, isResourceViewKind } from "./capabilities";
import type { ResourceViewKind } from "./capabilities";
import { Filter } from "./filter";
import type { ResourceViewSort, ResourceViewInitialState } from "./filter";
export interface ResourceViewFavorite {
  id: string;
  label: string;
  /** Put this saved filter in the list's quick filter row. */
  pinned?: boolean;
  pageSize?: number;
  sort?: ResourceViewSort | null;
  /** Opaque persisted intent; parsed by its query owner when applied. */
  filter?: unknown;
  groupStack?: unknown;
  view?: ResourceViewKind;
  /** Named preset whose fixed filter accompanies this saved view. */
  preset?: string;
  columnVisibility?: VisibilityState;
}

/** Code-owned view, sharing the saved-view state contract and query owner. */
export interface ResourceViewPreset extends ResourceViewFavorite {
  resource: string;
  /** Immutable while this preset is selected; never enters editable filter state. */
  fixedFilter?: QueryFilter;
}

const ResourceViewSortSchema = v.object({
  field: v.string(),
  dir: v.picklist(RESOURCE_VIEW_SORT_DIRECTIONS),
});

/** Parse boundary for one favorite stored inside the opaque preferences JSON. */
export const ResourceViewFavoriteSchema = v.object({
  id: v.string(),
  label: v.string(),
  pinned: v.optional(v.boolean()),
  pageSize: v.optional(v.pipe(v.number(), v.integer(), v.minValue(1))),
  sort: v.optional(v.nullable(ResourceViewSortSchema)),
  filter: v.optional(v.unknown()),
  groupStack: v.optional(v.unknown()),
  view: v.optional(v.custom<ResourceViewKind>((value) => typeof value === "string" && isResourceViewKind(value))),
  preset: v.optional(v.string()),
  columnVisibility: v.optional(v.record(v.string(), v.boolean())),
});

/** Validate shipped query intent eagerly, using the same owner as URL/favorite state. */
export function validateResourceViewPreset(preset: ResourceViewPreset, model: ModelMetadata): void {
  v.parse(ResourceViewFavoriteSchema, preset);
  if (!preset.label.trim()) throw new Error(`Resource view "${preset.id}" has an empty label.`);
  const query = ResourceQuery.from(model);
  query.filterFrom(preset.filter);
  query.filterFrom(preset.fixedFilter);
  query.groupsFrom(preset.groupStack ?? []);
  query.sortFrom(preset.sort ? [{ field: preset.sort.field, direction: preset.sort.dir === "desc" ? "DESC" : "ASC" }] : []);
  for (const field of Object.keys(preset.columnVisibility ?? {})) {
    if (!model.fields[field] && !model.resource.query.fields[field]) {
      throw new Error(`Resource view "${preset.id}" references unknown column "${field}".`);
    }
  }
}

/** Resolve a shipped preset only for the collection it declares. */
export function resourceViewPreset(
  presets: Readonly<Record<string, ResourceViewPreset>>,
  id: string | undefined,
  resource: string | undefined,
  allowedIds?: readonly string[],
): ResourceViewPreset | undefined {
  if (!id) return undefined;
  const preset = presets[id];
  if (!preset || preset.resource !== resource || (allowedIds && !allowedIds.includes(id))) {
    throw new Error(`Unknown resource view "${id}" for "${resource}".`);
  }
  return preset;
}

/** A selected shipped view seeds editable state; fixed filters stay on the declaration. */
export function resourceViewPresetDefaults(
  initial: ResourceViewInitialState | undefined,
  preset: ResourceViewPreset | undefined,
): ResourceViewInitialState {
  if (!preset) return initial ?? {};
  return {
    ...initial,
    preset: preset.id,
    ...(preset.pageSize === undefined ? {} : { pageSize: preset.pageSize }),
    ...(preset.view === undefined ? {} : { view: preset.view }),
    ...(preset.sort === undefined ? {} : { sort: preset.sort }),
    ...(preset.filter === undefined ? {} : { filter: Filter.from(preset.filter).value }),
    ...(preset.groupStack === undefined ? {} : { groupStack: v.parse(GroupSpecsSchema, preset.groupStack), groupStacks: undefined }),
    ...(preset.columnVisibility === undefined ? {} : { columnVisibility: preset.columnVisibility }),
  };
}

export function resourceViewFavoritesFromJson(
  raw: string | null,
): readonly ResourceViewFavorite[] {
  try {
    const value = raw ? JSON.parse(raw) : [];
    return resourceViewFavoritesFromUnknown(value);
  } catch {
    return [];
  }
}

export function resourceViewFavoritesFromUnknown(
  value: unknown,
): readonly ResourceViewFavorite[] {
  const arrayResult = v.safeParse(v.array(v.unknown()), value);
  if (!arrayResult.success) return [];
  return arrayResult.output.flatMap((item) => {
    const result = v.safeParse(ResourceViewFavoriteSchema, item);
    return result.success ? [result.output] : [];
  });
}

export function nextResourceViewFavoriteId(
  label: string,
  favorites: readonly ResourceViewFavorite[],
): string {
  const base = `favorite:${slugifyFavoriteLabel(label) || "search"}`;
  const existing = new Set(favorites.map((favorite) => favorite.id));
  if (!existing.has(base)) return base;
  for (let suffix = 2; ; suffix += 1) {
    const id = `${base}-${suffix}`;
    if (!existing.has(id)) return id;
  }
}

function slugifyFavoriteLabel(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}


/** Preserve favorites v1 while live table state uses TanStack's own types. */
export function favoriteFromResourceView(
  state: import("./state").ResourceViewState,
  label: string,
  existing: readonly ResourceViewFavorite[] = [],
): ResourceViewFavorite {
  const sort = state.sorting?.[0];
  return {
    id: nextResourceViewFavoriteId(label, existing),
    label,
    pageSize: state.pagination.pageSize,
    ...(state.preset ? { preset: state.preset } : {}),
    ...(Object.keys(state.columnVisibility).length ? { columnVisibility: state.columnVisibility } : {}),
    ...(sort ? { sort: { field: sort.id, dir: sort.desc ? "desc" as const : "asc" as const } } : {}),
    ...(Filter.from(state.filter).hasEntries() ? { filter: state.filter } : {}),
    ...(state.groupStack.length > 0 ? { groupStack: state.groupStack } : {}),
    ...(state.view !== "list" ? { view: state.view } : {}),
  };
}
