// English fallback strings for @angee/base primitives. The host runtime owns
// the active translations; these are the defaults used when a key is missing.

export const enBaseMessages: Record<string, string> = {
  "search.clear": "Clear search",
  "search.placeholder": "Search...",
};

export const enBaseBundle = { base: enBaseMessages } as const;
