import { useCallback, useSyncExternalStore } from "react";

import type { RuntimeComposition } from "./contracts";
import { useAppRuntime, useRuntimeUserPreferences } from "./runtime";

const PREFERENCE_KEY = "developerMode";
const SESSION_KEY = "angee:developer-mode";

// The browser session's override: `?debug=1|0` or the user-menu switch set it,
// and it wins over the stored preference until the tab closes. Memory holds it
// when storage is blocked.
const listeners = new Set<() => void>();
let memoryOverride: boolean | null = null;
function readOverride(): boolean | null {
  if (typeof window === "undefined") return null;
  try {
    const stored = window.sessionStorage.getItem(SESSION_KEY);
    return stored === null ? null : stored === "1";
  } catch {
    return memoryOverride;
  }
}
function writeOverride(on: boolean): void {
  memoryOverride = on;
  try {
    window.sessionStorage.setItem(SESSION_KEY, on ? "1" : "0");
  } catch {
    // Storage blocked: the memory override holds it for this page load.
  }
  for (const listener of listeners) listener();
}
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * The app router calls this on every navigation: `?debug=1` turns developer mode
 * on for the browser session and `?debug=0` turns it off, over the stored
 * preference; any other value changes nothing.
 */
export function applyDeveloperModeSearch(debug: unknown): void {
  if (debug !== "1" && debug !== "0") return;
  const on = debug === "1";
  if (readOverride() !== on) writeOverride(on);
}

/**
 * Whether developer mode is on: it reveals how the app was composed (menu ids
 * and provenance, hidden and removed entries, the active route, field names) and
 * changes nothing the server allows. The session override wins over the user's
 * stored preference.
 */
export function useDeveloperMode(): boolean {
  const override = useSyncExternalStore(subscribe, readOverride, () => null);
  const { preferences } = useRuntimeUserPreferences();
  return override ?? preferences[PREFERENCE_KEY] === true;
}

/**
 * The user-menu switch: it applies at once for the session and is stored in the
 * user's preferences when they can be written (not while viewing as another user).
 */
export function useDeveloperModeSwitch(): { enabled: boolean; setEnabled: (on: boolean) => void } {
  const enabled = useDeveloperMode();
  const { available, patchPreferences } = useRuntimeUserPreferences();
  const setEnabled = useCallback((on: boolean) => {
    writeOverride(on);
    // A failed write leaves the session override in place.
    if (available) void patchPreferences((current) => ({ ...current, [PREFERENCE_KEY]: on })).catch(() => undefined);
  }, [available, patchPreferences]);
  return { enabled, setEnabled };
}

/** The composition facts developer mode shows; `null` outside a composed app. */
export function useRuntimeComposition(): RuntimeComposition | null {
  return useAppRuntime().composition ?? null;
}
