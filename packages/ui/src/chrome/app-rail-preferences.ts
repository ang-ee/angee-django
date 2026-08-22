import { useCallback } from "react";
import * as v from "valibot";

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

const RailPreferencesEnvelopeSchema = v.object({
  order: v.optional(v.unknown()),
  defaultItemId: v.optional(v.unknown()),
  expanded: v.optional(v.unknown()),
});

export function readAppRailPreferences(
  preferences: RuntimeUserPreferences | null | undefined,
): AppRailPreferences {
  const raw = preferences?.[APP_RAIL_PREFERENCES_KEY];
  const result = v.safeParse(RailPreferencesEnvelopeSchema, raw);
  if (!result.success) return EMPTY_RAIL_PREFERENCES;
  const record = result.output;
  const defaultItemId = v.safeParse(v.string(), record.defaultItemId);
  const expanded = v.safeParse(v.boolean(), record.expanded);
  return {
    order: stringList(record.order),
    defaultItemId: defaultItemId.success ? defaultItemId.output : null,
    expanded: expanded.success ? expanded.output : undefined,
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
  const result = v.safeParse(v.array(v.unknown()), value);
  if (!result.success) return [];
  return dedupeBy(
    result.output.flatMap((item) => {
      const parsed = v.safeParse(v.string(), item);
      return parsed.success ? [parsed.output] : [];
    }),
    (item) => item,
  );
}
