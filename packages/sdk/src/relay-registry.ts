// A typename -> refetch registry. Each resource query registers its refetch
// under the model's GraphQL typename; a change event for that typename refetches
// every query bound to it. This covers the writes the normalized cache can't see
// on its own: deletes (whose payload carries no typename) and cross-actor pushes.
//
// The registry is observable: `typenames()` returns the set of models with at
// least one live query (a stable snapshot), and `subscribe()` notifies when that
// set changes — so a provider can open exactly one change subscription per model
// in view and close it when the last query unmounts.

export interface RefetchRegistry {
  register(typename: string, refetch: () => void): () => void;
  invalidate(typenames: readonly string[]): void;
  /** The models with at least one live query — a stable, sorted snapshot. */
  typenames(): readonly string[];
  /** Notify `listener` whenever the registered-typename set changes. */
  subscribe(listener: () => void): () => void;
}

export function createRefetchRegistry(): RefetchRegistry {
  const handlers = new Map<string, Set<() => void>>();
  const listeners = new Set<() => void>();
  let snapshot: readonly string[] = [];

  function refresh(): void {
    snapshot = [...handlers.keys()].sort();
    for (const listener of listeners) listener();
  }

  return {
    register(typename, refetch) {
      let set = handlers.get(typename);
      const isNew = set === undefined;
      if (set === undefined) {
        set = new Set();
        handlers.set(typename, set);
      }
      set.add(refetch);
      if (isNew) refresh();
      return () => {
        set.delete(refetch);
        if (set.size === 0) {
          handlers.delete(typename);
          refresh();
        }
      };
    },
    invalidate(typenames) {
      for (const typename of typenames) {
        const set = handlers.get(typename);
        if (!set) continue;
        for (const refetch of set) refetch();
      }
    },
    typenames() {
      return snapshot;
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
