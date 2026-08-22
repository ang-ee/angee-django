import { useCallback } from "react";
import { recordValue } from "@angee/refine";

import { dedupeBy } from "../lib/dedupe";
import {
  usePreferenceSlice,
  type RuntimeUserPreferences,
} from "../runtime";

export const APP_RAIL_PREFERENCES_KEY = "chrome.rail";

export interface AppRailPreferences {
  order: readonly string[];
  defaultItemId: string | null;
  expanded?: boolean;
}

const EMPTY_RAIL_PREFERENCES: AppRailPreferences = {
  order: [],
  defaultItemId: null,
  expanded: undefined,
};

export function readAppRailPreferences(
  preferences: RuntimeUserPreferences | null | undefined,
): AppRailPreferences {
  const raw = preferences?.[APP_RAIL_PREFERENCES_KEY];
  const record = recordValue(raw);
  if (!record) return EMPTY_RAIL_PREFERENCES;
  return {
    order: stringList(record.order),
    defaultItemId: typeof record.defaultItemId === "string"
      ? record.defaultItemId
      : null,
    expanded: typeof record.expanded === "boolean"
      ? record.expanded
      : undefined,
  };
}

export function writeAppRailPreferences(
  preferences: RuntimeUserPreferences,
  rail: AppRailPreferences,
): RuntimeUserPreferences {
  return {
    ...preferences,
    [APP_RAIL_PREFERENCES_KEY]: {
      order: [...rail.order],
      defaultItemId: rail.defaultItemId,
      ...(rail.expanded === undefined ? {} : { expanded: rail.expanded }),
    },
  };
}

export function useAppRailPreferences(): {
  railPreferences: AppRailPreferences;
  setRailPreferences: (rail: AppRailPreferences) => void;
} {
  const rail = usePreferenceSlice(
    APP_RAIL_PREFERENCES_KEY,
    readAppRailPreferences,
    writeAppRailPreferences,
  );

  const setRailPreferences = useCallback(
    (next: AppRailPreferences) => {
      void rail.update(() => next).catch(() => undefined);
    },
    [rail.update],
  );

  return {
    railPreferences: rail.value,
    setRailPreferences,
  };
}

function stringList(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return [];
  return dedupeBy(value.filter((item): item is string => typeof item === "string"), (item) => item);
}
