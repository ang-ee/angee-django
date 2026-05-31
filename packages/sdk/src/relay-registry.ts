// A typename -> refetch registry. Each resource query registers its refetch
// under the model's GraphQL typename; a change event for that typename refetches
// every query bound to it. This covers the writes the normalized cache can't see
// on its own: deletes (whose payload carries no typename) and cross-actor pushes.

export interface RefetchRegistry {
  register(typename: string, refetch: () => void): () => void;
  invalidate(typenames: readonly string[]): void;
}

export function createRefetchRegistry(): RefetchRegistry {
  const handlers = new Map<string, Set<() => void>>();

  return {
    register(typename, refetch) {
      const set = handlers.get(typename) ?? new Set();
      set.add(refetch);
      handlers.set(typename, set);
      return () => {
        set.delete(refetch);
        if (set.size === 0) handlers.delete(typename);
      };
    },
    invalidate(typenames) {
      for (const typename of typenames) {
        const set = handlers.get(typename);
        if (!set) continue;
        for (const refetch of set) refetch();
      }
    },
  };
}
