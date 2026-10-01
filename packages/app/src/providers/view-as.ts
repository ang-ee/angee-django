import { useSyncExternalStore } from "react";
import type { RuntimeViewAs } from "@angee/ui/runtime";

interface ViewAsSnapshot {
  viewAs: RuntimeViewAs["viewAs"];
  pending: boolean;
  error: string | null;
}

/** One app-local, memory-only target shared by React and the HTTP transport. */
export function createViewAsProvider(options: {
  /** Switch live delivery and reset queries synchronously, then verify identity. */
  changed: (userId: string | null) => Promise<void>;
}) {
  let snapshot: ViewAsSnapshot = { viewAs: null, pending: false, error: null };
  const listeners = new Set<() => void>();
  let generation = 0;
  const publish = (next: ViewAsSnapshot) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  const transition = async (userId: string | null): Promise<void> => {
    if (snapshot.pending || snapshot.viewAs?.userId === userId || (!snapshot.viewAs && userId === null)) return;
    const version = ++generation;
    publish({ viewAs: userId === null ? null : { userId }, pending: true, error: null });
    try {
      await options.changed(userId);
      if (version === generation) publish({ ...snapshot, pending: false });
    } catch {
      if (version !== generation) return;
      // An invalid/revoked target must not strand the session behind its header.
      // Restore the real actor, including when switching from another preview.
      if (userId !== null) {
        publish({ viewAs: null, pending: true, error: null });
        try { await options.changed(null); } catch { /* The identity query owns recovery. */ }
      }
      if (version === generation) publish({
        viewAs: null, pending: false,
        error: userId !== null ? "Could not preview this person." : "Could not refresh your session.",
      });
    }
  };
  return {
    getSnapshot: () => snapshot,
    getUserId: () => snapshot.viewAs?.userId ?? null,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    enter: (userId: string) => transition(userId),
    exit: () => transition(null),
    /** Login/logout invalidates pending transitions as well as the target. */
    reset() {
      generation += 1;
      publish({ viewAs: null, pending: false, error: null });
    },
  };
}

export type ViewAsProvider = ReturnType<typeof createViewAsProvider>;

export function useViewAsState(provider: ViewAsProvider) {
  return useSyncExternalStore(provider.subscribe, provider.getSnapshot, provider.getSnapshot);
}
