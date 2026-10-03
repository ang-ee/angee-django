import { useCallback, useSyncExternalStore } from "react";

import type { RuntimeBrand } from "./contracts";
import { useAppRuntime, type RuntimeUserPreferences } from "./runtime";
import { usePreferenceSlice } from "./user-preferences";

/**
 * How the composition came out, layer by layer: what developer mode shows. It
 * holds composition facts only (ids, addon names, route names), never records.
 */
export interface RuntimeComposition {
  shell: {
    home?: string;
    brand: RuntimeBrand | null;
    perspective: { id: string; root: string; home?: string } | null;
    /** The layer that supplied each resolved shell field. */
    provenance: Readonly<Record<string, string>>;
    diagnostics: readonly string[];
  };
  /** What the app runs with once deprecated `createApp` inputs override the shell. */
  effective: { home: string; confineTo: string | null };
  menus: {
    /** The layer that set each menu node field, declarations included. */
    provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
    /** Removed nodes with the removing layer, where they sat, and their label. */
    removed: readonly { id: string; route?: string; by: string; parent?: string; label?: string }[];
    /** Nodes left out of the rail, by a `hide` or by a layer's `only`. */
    hidden: readonly { id: string; by: string; reason: "hide" | "only" }[];
    /** Console routes a removal made unavailable, with the reason. */
    unavailable: Readonly<Record<string, string>>;
    diagnostics: readonly string[];
  };
}

export const DEVELOPER_MODE_PREFERENCE_KEY = "angee.developer-mode";
const SESSION_KEY = "angee:developer-mode";

// `?debug=1` switches developer mode on for the browser session, `?debug=0` off.
const sessionListeners = new Set<() => void>();
function readSession(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const debug = new URLSearchParams(window.location.search).get("debug");
    if (debug === "1") window.sessionStorage.setItem(SESSION_KEY, "1");
    if (debug === "0") window.sessionStorage.removeItem(SESSION_KEY);
    return window.sessionStorage.getItem(SESSION_KEY) === "1";
  } catch {
    return false;
  }
}
function writeSession(on: boolean): void {
  try {
    if (on) window.sessionStorage.setItem(SESSION_KEY, "1");
    else window.sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // Storage blocked: the stored preference still applies.
  }
  for (const listener of sessionListeners) listener();
}
function subscribeSession(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

const readPreference = (preferences: RuntimeUserPreferences): boolean =>
  preferences[DEVELOPER_MODE_PREFERENCE_KEY] === true;
const writePreference = (preferences: RuntimeUserPreferences, on: boolean): RuntimeUserPreferences =>
  ({ ...preferences, [DEVELOPER_MODE_PREFERENCE_KEY]: on });

/**
 * Developer mode reveals how the app was composed: menu ids and provenance,
 * hidden and removed entries, and the active route. Any signed-in user may turn
 * it on through the user menu (stored in their preferences) or with `?debug=1`
 * for the session; it changes nothing the server allows.
 */
export function useDeveloperMode(): { enabled: boolean; setEnabled: (on: boolean) => void } {
  const preference = usePreferenceSlice(DEVELOPER_MODE_PREFERENCE_KEY, readPreference, writePreference);
  const session = useSyncExternalStore(subscribeSession, readSession, () => false);
  const setEnabled = useCallback((on: boolean) => {
    if (!on || !preference.available) writeSession(on);
    if (preference.available) void preference.update(() => on);
  }, [preference]);
  return { enabled: session || preference.value, setEnabled };
}

/** The composition facts developer mode shows; `null` outside a composed app. */
export function useRuntimeComposition(): RuntimeComposition | null {
  return useAppRuntime().composition ?? null;
}
