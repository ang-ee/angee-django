import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { dedupeBy } from "../lib/dedupe";
import {
  type RuntimeUserPreferences,
  useRuntimeUserPreferences,
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
  if (!isObject(raw)) return EMPTY_RAIL_PREFERENCES;
  return {
    order: stringList(raw.order),
    defaultItemId: typeof raw.defaultItemId === "string"
      ? raw.defaultItemId
      : null,
    expanded: typeof raw.expanded === "boolean" ? raw.expanded : undefined,
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
  const { preferences, setPreferences } = useRuntimeUserPreferences();
  const [optimistic, setOptimistic] = useState<AppRailPreferences | null>(null);
  const latestWrite = useRef(0);
  const stored = useMemo(
    () => readAppRailPreferences(preferences),
    [preferences],
  );

  useEffect(() => {
    setOptimistic((current) =>
      current && sameAppRailPreferences(current, stored) ? null : current
    );
  }, [stored]);

  const setRailPreferences = useCallback(
    (rail: AppRailPreferences) => {
      const write = ++latestWrite.current;
      setOptimistic(rail);
      void setPreferences(writeAppRailPreferences(preferences, rail)).catch(() => {
        if (latestWrite.current !== write) return;
        setOptimistic((current) =>
          current && sameAppRailPreferences(current, rail) ? null : current
        );
      });
    },
    [preferences, setPreferences],
  );

  return {
    railPreferences: optimistic ?? stored,
    setRailPreferences,
  };
}

function sameAppRailPreferences(
  left: AppRailPreferences,
  right: AppRailPreferences,
): boolean {
  return left.defaultItemId === right.defaultItemId
    && left.expanded === right.expanded
    && left.order.length === right.order.length
    && left.order.every((item, index) => item === right.order[index]);
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringList(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return [];
  return dedupeBy(value.filter((item): item is string => typeof item === "string"), (item) => item);
}
