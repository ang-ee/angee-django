import * as v from "valibot";
import { RESOURCE_VIEW_GROUP_GRANULARITIES, RESOURCE_VIEW_KINDS, RESOURCE_VIEW_SORT_DIRECTIONS } from "./capabilities";
import type { ResourceViewKind } from "./capabilities";
import { isResourceViewFilter } from "./filter";
import type { ResourceViewFilter, ResourceViewGroup, ResourceViewSort } from "./filter";
export interface ResourceViewFavorite {
  id: string;
  label: string;
  pageSize?: number;
  sort?: ResourceViewSort | null;
  filter?: ResourceViewFilter;
  groupStack?: readonly ResourceViewGroup[];
  view?: ResourceViewKind;
}

const ResourceViewSortSchema = v.object({
  field: v.string(),
  dir: v.picklist(RESOURCE_VIEW_SORT_DIRECTIONS),
});

const ResourceViewGroupSchema = v.object({
  field: v.string(),
  aggregateField: v.optional(v.string()),
  aggregateKey: v.optional(v.string()),
  granularity: v.optional(v.picklist(RESOURCE_VIEW_GROUP_GRANULARITIES)),
});

/** Parse boundary for one favorite stored inside the opaque preferences JSON. */
export const ResourceViewFavoriteSchema = v.object({
  id: v.string(),
  label: v.string(),
  pageSize: v.optional(v.pipe(v.number(), v.finite())),
  sort: v.optional(v.nullable(ResourceViewSortSchema)),
  filter: v.optional(v.custom<ResourceViewFilter>(isResourceViewFilter)),
  groupStack: v.optional(v.array(ResourceViewGroupSchema)),
  view: v.optional(v.picklist(RESOURCE_VIEW_KINDS)),
});

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
