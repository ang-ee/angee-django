import type {
  RuntimeUserPreferences,
  RuntimeUserPreferencesPatch,
} from "@angee/ui/runtime";

export interface UserPreferencesPatchQueue {
  patch: (apply: RuntimeUserPreferencesPatch) => Promise<void>;
  rebase: (preferences: RuntimeUserPreferences) => void;
  close: () => void;
}

export interface CreateUserPreferencesPatchQueueOptions {
  persist: (
    preferences: RuntimeUserPreferences,
  ) => Promise<RuntimeUserPreferences>;
  committed: (preferences: RuntimeUserPreferences) => void;
}

/** Serialize whole-document writes while exposing a functional patch contract. */
export function createUserPreferencesPatchQueue({
  persist,
  committed,
}: CreateUserPreferencesPatchQueueOptions): UserPreferencesPatchQueue {
  let current: RuntimeUserPreferences = {};
  let closed = false;
  let tail = Promise.resolve();

  return {
    patch(apply) {
      const operation = tail.catch(() => undefined).then(async () => {
        if (closed) throw new UserPreferencesQueueClosedError();
        const next = apply(current);
        const saved = await persist(next);
        if (closed) return;
        current = saved;
        committed(saved);
      });
      tail = operation;
      return operation;
    },
    rebase(preferences) {
      if (closed) return;
      current = preferences;
    },
    close() {
      closed = true;
    },
  };
}

export class UserPreferencesQueueClosedError extends Error {
  constructor() {
    super("User preferences queue was superseded before persistence.");
    this.name = "UserPreferencesQueueClosedError";
  }
}
