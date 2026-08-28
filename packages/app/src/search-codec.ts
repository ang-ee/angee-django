import { routeSearchString } from "@angee/ui/runtime";

// Keep search values flat and unquoted so login next round-trips raw and
// resource-view values read like status:year.
export function parseFlatSearch(searchStr: string): Record<string, string> {
  const source = searchStr.startsWith("?") ? searchStr.slice(1) : searchStr;
  const params = new URLSearchParams(source);
  const search: Record<string, string> = {};
  for (const [key, value] of params.entries()) {
    search[key] = value;
  }
  return search;
}

export function stringifyFlatSearch(search: Record<string, unknown>): string {
  const query = routeSearchString(search);
  return query ? `?${query}` : "";
}
