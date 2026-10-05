import { vi } from "vitest";
import type { ResourceSearch, SearchCatalog } from "./types";

/** A controlled presentation fixture; provider transitions have their own tests. */
export function searchFixture({ catalog, ...commands }: Partial<Omit<ResourceSearch, "catalog">> & { catalog?: Partial<SearchCatalog> } = {}): ResourceSearch {
  return {
    catalog: { text: [{ field: "title", label: "Title" }], filters: [], facets: [], fields: [], groups: [], curatedGroups: [], favorites: [], ...catalog },
    active: [], groupStack: [], groupingEnabled: false, queryDirty: false, filter: {}, setFilter: vi.fn(),
    setText: vi.fn(), toggleFilter: vi.fn(), setFacet: vi.fn(), toggleFacetOption: vi.fn(),
    addClause: vi.fn(), setClause: vi.fn(), addGroup: vi.fn(), removeGroup: vi.fn(),
    moveGroup: vi.fn(), setGroupLevel: vi.fn(), setGroupStack: vi.fn(),
    applyFavorite: vi.fn(), toggleFavorite: vi.fn(), clear: vi.fn(),
    saveFavorite: undefined, renameFavorite: undefined, pinFavorite: undefined,
    resetQuery: vi.fn(), clearQuery: vi.fn(), ...commands,
  };
}
