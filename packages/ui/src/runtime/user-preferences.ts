import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
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
  generation: object;
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
  const generation = useMemo(
    () => ({}),
    [available, key, patchPreferences],
  );
  const [optimistic, setOptimistic] =
    useState<OptimisticPreferences | null>(null);
  // Armed while mounted, re-armed by the effect after StrictMode's simulated
  // remount — a memoized AbortController aborted in cleanup stays dead forever
  // there, silently swallowing every update.
  const closedRef = useRef(false);
  useEffect(() => {
    closedRef.current = false;
    return () => {
      closedRef.current = true;
    };
  }, [generation]);

  const displayed = optimistic?.generation === generation
    ? optimistic.preferences
    : preferences;

  const update = useCallback(
    async (apply: (current: TValue) => TValue): Promise<void> => {
      if (!available || closedRef.current) return;
      const applyPreferences: RuntimeUserPreferencesPatch = (current) =>
        write(current, apply(read(current)));
      const operation = {};
      setOptimistic((current) => ({
        operation,
        generation,
        preferences: applyPreferences(
          current?.generation === generation ? current.preferences : preferences,
        ),
      }));
      try {
        await patchPreferences(applyPreferences);
      } finally {
        if (!closedRef.current) {
          setOptimistic((current) =>
            current?.operation === operation ? null : current
          );
        }
      }
    },
    [available, generation, patchPreferences, preferences, read, write],
  );

  return { available, value: read(displayed), update };
}
