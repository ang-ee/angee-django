/** Storybook URL args/globals allow alphanumerics, spaces, underscores and hyphens. */
export function storybookValueLookup(ids: readonly string[]): ReadonlyMap<string, string> {
  return new Map(ids.map(id => [
    encodeURIComponent(id).replaceAll("_", "%5F").replaceAll(".", "%2E")
      .replaceAll(/[!'()*]/g, char => `%${char.charCodeAt(0).toString(16).toUpperCase()}`).replaceAll("%", "_"),
    id,
  ]));
}
