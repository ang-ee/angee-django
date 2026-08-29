import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = Exclude<ThemePreference, "system">;

export const THEME_STORAGE_KEY = "angee:theme";

const THEME_CHANGE_EVENT = "angee:themechange";
const SERVER_THEME_SNAPSHOT = "system:light:light" satisfies ThemeSnapshot;

type ThemeSnapshot = `${ThemePreference}:${ResolvedTheme}:${ResolvedTheme}`;

let fallbackThemePreference: ThemePreference | null = null;

export interface ThemeState {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  system: ResolvedTheme;
  setPreference: (value: ThemePreference) => void;
}

export function normaliseThemePreference(
  value: string | null | undefined,
): ThemePreference | null {
  return value === "light" || value === "dark" || value === "system"
    ? value
    : null;
}

export function storedThemePreference(): ThemePreference {
  const storage = themeStorage();
  if (!storage) return fallbackThemePreference ?? "system";
  try {
    return (
      normaliseThemePreference(storage.getItem(THEME_STORAGE_KEY)) ??
      fallbackThemePreference ??
      "system"
    );
  } catch {
    return fallbackThemePreference ?? "system";
  }
}

export function setThemePreference(value: ThemePreference): void {
  fallbackThemePreference = value;
  const storage = themeStorage();
  if (storage) {
    try {
      storage.setItem(THEME_STORAGE_KEY, value);
    } catch {
      // Keep the in-memory fallback so the current tab still responds.
    }
  }
  applyThemePreference(value);
  notifyThemeSubscribers();
}

export function applyThemePreference(value: ThemePreference): ResolvedTheme {
  const resolved = resolvedThemePreference(value);
  if (typeof document !== "undefined") {
    document.documentElement.dataset.theme = resolved;
  }
  return resolved;
}

export function resolvedThemePreference(value: ThemePreference): ResolvedTheme {
  if (value !== "system") return value;
  return systemThemePreference();
}

export function systemThemePreference(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function useThemePreference(): ThemeState {
  const snapshot = useSyncExternalStore<ThemeSnapshot>(
    subscribeTheme,
    themeSnapshot,
    () => SERVER_THEME_SNAPSHOT,
  );
  const state = useMemo(() => parseThemeSnapshot(snapshot), [snapshot]);
  const setPreference = useCallback((value: ThemePreference) => {
    setThemePreference(value);
  }, []);

  useEffect(() => {
    applyThemePreference(state.preference);
  }, [state.preference, state.resolved]);

  return useMemo(
    () => ({ ...state, setPreference }),
    [setPreference, state],
  );
}

function themeSnapshot(): ThemeSnapshot {
  const preference = storedThemePreference();
  const system = systemThemePreference();
  const resolved = preference === "system" ? system : preference;
  return `${preference}:${resolved}:${system}` as ThemeSnapshot;
}

function parseThemeSnapshot(
  snapshot: ThemeSnapshot,
): Omit<ThemeState, "setPreference"> {
  const [preference, resolved, system] = snapshot.split(":");
  return {
    preference: normaliseThemePreference(preference) ?? "system",
    resolved: resolved === "dark" ? "dark" : "light",
    system: system === "dark" ? "dark" : "light",
  };
}

function subscribeTheme(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;

  const onThemeChange = () => {
    applyThemePreference(storedThemePreference());
    listener();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== THEME_STORAGE_KEY) return;
    onThemeChange();
  };
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");

  window.addEventListener(THEME_CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  query?.addEventListener("change", onThemeChange);

  return () => {
    window.removeEventListener(THEME_CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
    query?.removeEventListener("change", onThemeChange);
  };
}

function notifyThemeSubscribers(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(THEME_CHANGE_EVENT));
}

function themeStorage(): Storage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage ?? null;
  } catch {
    return null;
  }
}
