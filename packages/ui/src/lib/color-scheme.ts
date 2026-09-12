import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import type { ColorScheme, ColorSchemePreference } from "../theme/runtime.mjs";
export type { ColorScheme, ColorSchemePreference } from "../theme/runtime.mjs";

export const COLOR_SCHEME_STORAGE_KEY = "angee:color-scheme";
export const LEGACY_THEME_STORAGE_KEY = "angee:theme";
export const COLOR_SCHEME_CHANGE_EVENT = "angee:color-scheme-change";
const SERVER_SNAPSHOT = "system:light:light" satisfies ColorSchemeSnapshot;
type ColorSchemeSnapshot = `${ColorSchemePreference}:${ColorScheme}:${ColorScheme}`;
let fallbackPreference: ColorSchemePreference | null = null;

export interface ColorSchemeState { preference: ColorSchemePreference; resolved: ColorScheme; system: ColorScheme; setPreference: (value: ColorSchemePreference) => void }
export function normaliseColorSchemePreference(value: string | null | undefined): ColorSchemePreference | null { return value === "light" || value === "dark" || value === "system" ? value : null; }
export function storedColorSchemePreference(): ColorSchemePreference {
  const storage = browserStorage();
  if (!storage) return fallbackPreference ?? "system";
  try { return normaliseColorSchemePreference(storage.getItem(COLOR_SCHEME_STORAGE_KEY)) ?? normaliseColorSchemePreference(storage.getItem(LEGACY_THEME_STORAGE_KEY)) ?? fallbackPreference ?? "system"; }
  catch { return fallbackPreference ?? "system"; }
}
export function setColorSchemePreference(value: ColorSchemePreference): void {
  fallbackPreference = value;
  const event = typeof window === "undefined" ? null : new CustomEvent(COLOR_SCHEME_CHANGE_EVENT, { cancelable: true, detail: { preference: value } });
  if (event && !window.dispatchEvent(event)) return;
  const storage = browserStorage();
  if (storage) { try { storage.setItem(COLOR_SCHEME_STORAGE_KEY, value); } catch { /* memory fallback remains */ } }
  applyColorSchemePreference(value);
  if (typeof window !== "undefined") window.dispatchEvent(new Event(`${COLOR_SCHEME_CHANGE_EVENT}:applied`));
}
export function applyColorSchemePreference(value: ColorSchemePreference, target?: Document): ColorScheme {
  const resolved = resolveColorSchemePreference(value);
  if (typeof document !== "undefined") {
    const root = (target ?? document).documentElement;
    root.dataset.colorScheme = resolved;
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
  }
  return resolved;
}
export function resolveColorSchemePreference(value: ColorSchemePreference): ColorScheme { return value === "system" ? systemColorScheme() : value; }
export function systemColorScheme(): ColorScheme { return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"; }
export function useColorSchemePreference(): ColorSchemeState {
  const snapshot = useSyncExternalStore<ColorSchemeSnapshot>(subscribe, getSnapshot, () => SERVER_SNAPSHOT);
  const state = useMemo(() => parseSnapshot(snapshot), [snapshot]);
  const setPreference = useCallback((value: ColorSchemePreference) => setColorSchemePreference(value), []);
  useEffect(() => { applyColorSchemePreference(state.preference); }, [state.preference, state.resolved]);
  return useMemo(() => ({ ...state, setPreference }), [setPreference, state]);
}
function getSnapshot(): ColorSchemeSnapshot { const preference = storedColorSchemePreference(); const system = systemColorScheme(); return `${preference}:${preference === "system" ? system : preference}:${system}` as ColorSchemeSnapshot; }
function parseSnapshot(snapshot: ColorSchemeSnapshot): Omit<ColorSchemeState, "setPreference"> { const [preference, resolved, system] = snapshot.split(":"); return { preference: normaliseColorSchemePreference(preference) ?? "system", resolved: resolved === "dark" ? "dark" : "light", system: system === "dark" ? "dark" : "light" }; }
function subscribe(listener: () => void): () => void {
  if (typeof window === "undefined") return () => undefined;
  const onChange = () => { applyColorSchemePreference(storedColorSchemePreference()); listener(); };
  const onStorage = (event: StorageEvent) => { if (event.key === null || event.key === COLOR_SCHEME_STORAGE_KEY || event.key === LEGACY_THEME_STORAGE_KEY) onChange(); };
  const query = window.matchMedia?.("(prefers-color-scheme: dark)");
  window.addEventListener(`${COLOR_SCHEME_CHANGE_EVENT}:applied`, listener); window.addEventListener("storage", onStorage); query?.addEventListener("change", onChange);
  return () => { window.removeEventListener(`${COLOR_SCHEME_CHANGE_EVENT}:applied`, listener); window.removeEventListener("storage", onStorage); query?.removeEventListener("change", onChange); };
}
function browserStorage(): Storage | null { if (typeof window === "undefined") return null; try { return window.localStorage ?? null; } catch { return null; } }
