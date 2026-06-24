import { useCallback, useEffect, useMemo, useState } from "react";
import { type UserPreferences, useUserPreferences } from "@angee/data";

import { dedupeBy } from "../lib/dedupe";

export const APP_RAIL_PREFERENCES_KEY = "chrome.rail";

export interface AppRailPreferences {
  order: readonly string[];
  defaultItemId: string | null;
}

const EMPTY_RAIL_PREFERENCES: AppRailPreferences = {
  order: [],
  defaultItemId: null,
};

export function readAppRailPreferences(
  preferences: UserPreferences | null | undefined,
): AppRailPreferences {
  const raw = preferences?.[APP_RAIL_PREFERENCES_KEY];
  if (!isObject(raw)) return EMPTY_RAIL_PREFERENCES;
  return {
    order: stringList(raw.order),
    defaultItemId: typeof raw.defaultItemId === "string"
      ? raw.defaultItemId
      : null,
  };
}

export function writeAppRailPreferences(
  preferences: UserPreferences,
  rail: AppRailPreferences,
): UserPreferences {
  return {
    ...preferences,
    [APP_RAIL_PREFERENCES_KEY]: {
      order: [...rail.order],
      defaultItemId: rail.defaultItemId,
    },
  };
}

export function useAppRailPreferences(): {
  railPreferences: AppRailPreferences;
  setRailPreferences: (rail: AppRailPreferences) => void;
} {
  const { preferences, setPreferences } = useUserPreferences();
  const [optimistic, setOptimistic] = useState<AppRailPreferences | null>(null);
  const stored = useMemo(
    () => readAppRailPreferences(preferences),
    [preferences],
  );

  useEffect(() => {
    setOptimistic(null);
  }, [stored]);

  const setRailPreferences = useCallback(
    (rail: AppRailPreferences) => {
      setOptimistic(rail);
      void setPreferences(writeAppRailPreferences(preferences, rail)).catch(() => {
        setOptimistic(null);
      });
    },
    [preferences, setPreferences],
  );

  return {
    railPreferences: optimistic ?? stored,
    setRailPreferences,
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringList(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return [];
  return dedupeBy(value.filter((item): item is string => typeof item === "string"), (item) => item);
}
