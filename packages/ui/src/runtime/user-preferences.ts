import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  useRuntimeUserPreferences,
  type RuntimeUserPreferences,
  type RuntimeUserPreferencesPatch,
} from "./runtime";

export interface PreferenceSliceState<TValue> {
  available: boolean;
  value: TValue;
  update: (apply: (current: TValue) => TValue) => Promise<void>;
}

interface OptimisticPreferences {
  operation: object;
  preferences: RuntimeUserPreferences;
  scope: AbortController;
}

/** Optimistic display and rollback for one slice of the runtime preference document. */
export function usePreferenceSlice<TValue>(
  key: string,
  read: (preferences: RuntimeUserPreferences) => TValue,
  write: (
    preferences: RuntimeUserPreferences,
    value: TValue,
  ) => RuntimeUserPreferences,
): PreferenceSliceState<TValue> {
  const { available, preferences, patchPreferences } =
    useRuntimeUserPreferences();
  const scope = useMemo(
    () => new AbortController(),
    [available, key, patchPreferences],
  );
  const [optimistic, setOptimistic] =
    useState<OptimisticPreferences | null>(null);

  useEffect(() => () => scope.abort(), [scope]);

  const displayed = optimistic?.scope === scope
    ? optimistic.preferences
    : preferences;

  const update = useCallback(
    async (apply: (current: TValue) => TValue): Promise<void> => {
      if (!available || scope.signal.aborted) return;
      const applyPreferences: RuntimeUserPreferencesPatch = (current) =>
        write(current, apply(read(current)));
      const operation = {};
      setOptimistic((current) => ({
        operation,
        scope,
        preferences: applyPreferences(
          current?.scope === scope ? current.preferences : preferences,
        ),
      }));
      try {
        await patchPreferences(applyPreferences);
      } finally {
        if (!scope.signal.aborted) {
          setOptimistic((current) =>
            current?.operation === operation ? null : current
          );
        }
      }
    },
    [available, patchPreferences, preferences, read, scope, write],
  );

  return { available, value: read(displayed), update };
}
