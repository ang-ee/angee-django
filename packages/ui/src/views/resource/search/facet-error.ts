import type { SearchFacet } from "./types";

/** A facet's own read or decode `error` as the search panel and its shortcut show it: its message, or nothing. */
export function facetError(error: Error | null | undefined): Pick<SearchFacet, "error"> {
  return error ? { error: error.message } : {};
}
