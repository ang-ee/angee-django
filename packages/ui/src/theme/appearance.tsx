import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import {
  resolveThemeOptions,
  type ColorScheme,
  type ColorSchemePreference,
  type ThemeOptionsEnvelope,
  type ThemeTokenName,
  THEME_TOKEN_NAMES,
} from "./runtime.mjs";
import type { ThemeContribution } from "./index";
import {
  COLOR_SCHEME_CHANGE_EVENT,
  LEGACY_THEME_STORAGE_KEY,
  normaliseColorSchemePreference,
} from "../lib/color-scheme";
import {
  useAppRuntime,
  useRuntimeAuth,
  useRuntimeUserPreferences,
  type RuntimeUserPreferences,
} from "../runtime/runtime";

export const APPEARANCE_PREFERENCE_KEY = "appearance";
export const APPEARANCE_CACHE_KEY = "angee:appearance";
export const APPEARANCE_CACHE_SCHEMA = 1;
export const APPEARANCE_CACHE_LIMIT = 128 * 1024;

export interface AppearancePreferences {
  version: 1;
  themeId?: string;
  colorScheme?: ColorSchemePreference;
  options?: ThemeOptionsEnvelope;
}

export interface HostAppearanceDefaults {
  themeId?: string | null;
  colorScheme?: ColorSchemePreference;
  options?: ThemeOptionsEnvelope;
  fingerprint?: string;
}

export interface AppearanceState {
  preferences: AppearancePreferences;
  effectiveThemeId: string | null;
  effectiveColorSchemePreference: ColorSchemePreference;
  colorScheme: ColorScheme;
  theme: ThemeContribution | null;
  available: boolean;
  editable: boolean;
  resolvingIdentity: boolean;
  saving: boolean;
  error: Error | null;
  notice: "theme-unavailable" | "options-invalid" | "version-unsupported" | null;
  setTheme: (themeId: string | undefined, options?: ThemeOptionsEnvelope) => Promise<void>;
  setColorScheme: (preference: ColorSchemePreference | undefined) => Promise<void>;
  setOptions: (options: ThemeOptionsEnvelope) => Promise<void>;
  reset: () => Promise<void>;
}

const EMPTY_PREFERENCES: AppearancePreferences = { version: 1 };
const DEFAULT_HOST: Required<Pick<HostAppearanceDefaults, "themeId" | "colorScheme">> = {
  themeId: null,
  colorScheme: "system",
};
const AppearanceContext = createContext<AppearanceState | null>(null);

interface PendingLegacyColorScheme {
  actorId: string | null;
  preference: ColorSchemePreference;
}

export interface AppearancePreferenceRead {
  value: AppearancePreferences;
  notice: AppearanceState["notice"];
  writable: boolean;
  hasRawOptions: boolean;
  rawOptions?: unknown;
}

export function AppearanceProvider({ children, host = DEFAULT_HOST }: { children: ReactNode; host?: HostAppearanceDefaults }): ReactNode {
  const runtime = useAppRuntime();
  const auth = useRuntimeAuth();
  const userPreferences = useRuntimeUserPreferences();
  const catalogue = useMemo(
    () => new Map(runtime.themes.map((theme) => [theme.definition.id, theme])),
    [runtime.themes],
  );
  const saved = readAppearancePreferences(userPreferences.preferences);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [pendingLegacy, setPendingLegacy] = useState<PendingLegacyColorScheme | null>(readPendingLegacyColorScheme);
  const system = useSystemColorScheme();
  const hostThemeId = host.themeId ?? null;
  const selectedThemeId = saved.value.themeId === undefined ? hostThemeId : saved.value.themeId;
  const selectedTheme = selectedThemeId ? catalogue.get(selectedThemeId) ?? null : null;
  const colorSchemePreference = saved.value.colorScheme ?? host.colorScheme ?? "system";
  const colorScheme = colorSchemePreference === "system" ? system : colorSchemePreference;
  let notice = saved.notice;
  if (selectedThemeId && !selectedTheme) notice ??= "theme-unavailable";
  const effectiveThemeId = selectedTheme ? selectedThemeId : catalogue.has(hostThemeId ?? "") ? hostThemeId : null;
  const effectiveTheme = effectiveThemeId ? catalogue.get(effectiveThemeId) ?? null : null;
  const requestedOptions = saved.value.themeId === undefined ? host.options : saved.value.options;
  let effectiveTokens: Partial<Record<ThemeTokenName, string>> = {};
  let cacheOptions: ThemeOptionsEnvelope | undefined;
  if (effectiveTheme) {
    try {
      const resolved = resolveThemeOptions(effectiveTheme.definition, requestedOptions);
      effectiveTokens = { ...resolved.tokens.shared, ...resolved.tokens[colorScheme] };
      if (effectiveTheme.definition.options) cacheOptions = { version: resolved.version, value: resolved.value };
    } catch {
      notice ??= "options-invalid";
      const resolved = resolveThemeOptions(effectiveTheme.definition);
      effectiveTokens = { ...resolved.tokens.shared, ...resolved.tokens[colorScheme] };
      if (effectiveTheme.definition.options) cacheOptions = { version: resolved.version, value: resolved.value };
    }
  }

  const resolvingIdentity = auth.status === "resolving";
  useEffect(() => {
    if (resolvingIdentity) return;
    applyAppearanceRoot({ themeId: effectiveThemeId, colorScheme, tokens: effectiveTokens });
  }, [colorScheme, effectiveThemeId, effectiveTokens, resolvingIdentity]);

  useEffect(() => {
    if (resolvingIdentity) return;
    writeAppearanceCache({
      schema: APPEARANCE_CACHE_SCHEMA,
      fingerprint: host.fingerprint ?? "",
      actorId: auth.user?.id ?? "anonymous",
      themeId: effectiveThemeId,
      colorSchemePreference,
      colorScheme,
      options: cacheOptions,
      tokens: effectiveTokens,
      ...(pendingLegacy ? { pendingLegacy } : {}),
    });
  }, [auth.user?.id, cacheOptions, colorScheme, colorSchemePreference, effectiveThemeId, effectiveTokens, host.fingerprint, pendingLegacy, resolvingIdentity]);

  const update = useCallback(async (
    apply: (current: AppearancePreferences) => AppearancePreferences | undefined,
    operation: "edit" | "scheme" | "theme" | "reset" = "edit",
  ) => {
    if (!userPreferences.available) return;
    setSaving(true);
    setError(null);
    try {
      await userPreferences.patchPreferences((current) => {
        const decoded = readAppearancePreferences(current);
        if (!decoded.writable && operation !== "reset") {
          throw new Error("This appearance preference was written by a newer version. Reset it before editing.");
        }
        const next = apply(decoded.value);
        if (operation === "scheme" && next && decoded.hasRawOptions && next.options === undefined) {
          return writeAppearancePreferences(current, { ...next, options: decoded.rawOptions });
        }
        return writeAppearancePreferences(current, next);
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)));
      throw caught;
    } finally {
      setSaving(false);
    }
  }, [userPreferences]);

  const setTheme = useCallback(async (themeId: string | undefined, options?: ThemeOptionsEnvelope) => {
    await update((current) => {
      if (themeId === undefined) {
        const { themeId: _theme, options: _options, ...rest } = current;
        return rest;
      }
      const definition = catalogue.get(themeId)?.definition;
      if (!definition) throw new Error(`Theme ${JSON.stringify(themeId)} is not installed.`);
      if (options && !definition.options) throw new Error(`Theme ${JSON.stringify(themeId)} does not accept options.`);
      const resolved = definition.options ? resolveThemeOptions(definition, options) : null;
      return {
        ...current,
        themeId,
        ...(resolved ? { options: { version: resolved.version, value: resolved.value } } : { options: undefined }),
      };
    }, "theme");
  }, [catalogue, update]);
  const setColorScheme = useCallback(async (preference: ColorSchemePreference | undefined) => {
    await update((current) => {
      if (preference === undefined) {
        const { colorScheme: _scheme, ...rest } = current;
        return rest;
      }
      return { ...current, colorScheme: preference };
    }, "scheme");
  }, [update]);
  const setOptions = useCallback(async (options: ThemeOptionsEnvelope) => {
    await update((current) => current.themeId ? { ...current, options } : current);
  }, [update]);
  const reset = useCallback(async () => update(() => undefined, "reset"), [update]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const onCommand = (event: Event) => {
      const preference = normaliseColorSchemePreference((event as CustomEvent<{ preference?: string }>).detail?.preference);
      if (!preference) return;
      event.preventDefault();
      void setColorScheme(preference).catch(() => undefined);
    };
    window.addEventListener(COLOR_SCHEME_CHANGE_EVENT, onCommand);
    return () => window.removeEventListener(COLOR_SCHEME_CHANGE_EVENT, onCommand);
  }, [setColorScheme]);

  useEffect(() => {
    if (!pendingLegacy || resolvingIdentity || auth.status !== "authenticated" || !auth.user?.id || !userPreferences.available) return;
    const actorId = auth.user.id;
    if (pendingLegacy.actorId === null) {
      setPendingLegacy({ ...pendingLegacy, actorId });
      return;
    }
    if (pendingLegacy.actorId !== actorId) return;
    if (!saved.writable || saved.value.colorScheme !== undefined) {
      clearLegacyColorScheme();
      setPendingLegacy(null);
      return;
    }
    let active = true;
    void setColorScheme(pendingLegacy.preference).then(() => {
      if (!active) return;
      clearLegacyColorScheme();
      setPendingLegacy(null);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [auth.status, auth.user?.id, pendingLegacy, resolvingIdentity, saved.value.colorScheme, saved.writable, setColorScheme, userPreferences.available]);

  const value = useMemo<AppearanceState>(() => ({
    preferences: saved.value,
    effectiveThemeId,
    effectiveColorSchemePreference: colorSchemePreference,
    colorScheme,
    theme: effectiveTheme,
    available: userPreferences.available,
    editable: saved.writable,
    resolvingIdentity,
    saving,
    error,
    notice,
    setTheme,
    setColorScheme,
    setOptions,
    reset,
  }), [saved.value, effectiveThemeId, colorSchemePreference, colorScheme, effectiveTheme, userPreferences.available, saved.writable, resolvingIdentity, saving, error, notice, setTheme, setColorScheme, setOptions, reset]);
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

export function useAppearance(): AppearanceState {
  const value = useContext(AppearanceContext);
  if (!value) throw new Error("Appearance is unavailable: render within AppearanceProvider.");
  return value;
}

export function useOptionalAppearance(): AppearanceState | null {
  return useContext(AppearanceContext);
}

export function readAppearancePreferences(preferences: RuntimeUserPreferences): AppearancePreferenceRead {
  const raw = preferences[APPEARANCE_PREFERENCE_KEY];
  if (raw === undefined) return { value: EMPTY_PREFERENCES, notice: null, writable: true, hasRawOptions: false };
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { value: EMPTY_PREFERENCES, notice: "version-unsupported", writable: false, hasRawOptions: false };
  const source = raw as Record<string, unknown>;
  if (source.version !== 1) return { value: EMPTY_PREFERENCES, notice: "version-unsupported", writable: false, hasRawOptions: false };
  const themeId = typeof source.themeId === "string" && source.themeId.length <= 160 ? source.themeId : undefined;
  const colorScheme = source.colorScheme === "light" || source.colorScheme === "dark" || source.colorScheme === "system" ? source.colorScheme : undefined;
  const options = readOptionsEnvelope(source.options);
  return {
    value: { version: 1, ...(themeId ? { themeId } : {}), ...(colorScheme ? { colorScheme } : {}), ...(options ? { options } : {}) },
    notice: source.options !== undefined && !options ? "options-invalid" : null,
    writable: true,
    hasRawOptions: Object.prototype.hasOwnProperty.call(source, "options"),
    rawOptions: source.options,
  };
}

export function writeAppearancePreferences(preferences: RuntimeUserPreferences, appearance: AppearancePreferences | (Omit<AppearancePreferences, "options"> & { options?: unknown }) | undefined): RuntimeUserPreferences {
  if (appearance === undefined) {
    const { [APPEARANCE_PREFERENCE_KEY]: _appearance, ...rest } = preferences;
    return rest;
  }
  return { ...preferences, [APPEARANCE_PREFERENCE_KEY]: appearance };
}

export function applyAppearanceRoot({ themeId, colorScheme, tokens, target }: { themeId: string | null; colorScheme: ColorScheme; tokens?: Partial<Record<ThemeTokenName, string>>; target?: Document }): void {
  if (typeof document === "undefined" && !target) return;
  const root = (target ?? document).documentElement;
  if (themeId) root.dataset.themeId = themeId; else delete root.dataset.themeId;
  root.dataset.colorScheme = colorScheme;
  root.dataset.theme = colorScheme;
  root.style.colorScheme = colorScheme;
  for (const name of THEME_TOKEN_NAMES) root.style.removeProperty(name);
  for (const [name, value] of Object.entries(tokens ?? {})) if (value !== undefined) root.style.setProperty(name, value);
}

export function clearAppearanceCache(): void {
  if (typeof window === "undefined") return;
  try { window.localStorage.removeItem(APPEARANCE_CACHE_KEY); } catch { /* storage is disposable */ }
}

function writeAppearanceCache(value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    const encoded = JSON.stringify(value);
    if (encoded.length <= APPEARANCE_CACHE_LIMIT) window.localStorage.setItem(APPEARANCE_CACHE_KEY, encoded);
  } catch { /* storage is disposable */ }
}

function readPendingLegacyColorScheme(): PendingLegacyColorScheme | null {
  if (typeof window === "undefined") return null;
  try {
    const current = window.localStorage.getItem(APPEARANCE_CACHE_KEY);
    if (current !== null) {
      if (current.length > APPEARANCE_CACHE_LIMIT) return null;
      const parsed = JSON.parse(current) as { pendingLegacy?: unknown };
      const pending = parsed?.pendingLegacy;
      if (!pending || typeof pending !== "object" || Array.isArray(pending)) return null;
      const source = pending as Record<string, unknown>;
      const preference = normaliseColorSchemePreference(typeof source.preference === "string" ? source.preference : null);
      const actorId = source.actorId === null || typeof source.actorId === "string" ? source.actorId : undefined;
      return preference && actorId !== undefined ? { preference, actorId } : null;
    }
    const preference = normaliseColorSchemePreference(window.localStorage.getItem(LEGACY_THEME_STORAGE_KEY));
    return preference ? { preference, actorId: null } : null;
  } catch {
    return null;
  }
}

function clearLegacyColorScheme(): void {
  if (typeof window === "undefined") return;
  try { window.localStorage.removeItem(LEGACY_THEME_STORAGE_KEY); } catch { /* storage is disposable */ }
}

function readOptionsEnvelope(value: unknown): ThemeOptionsEnvelope | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const source = value as Record<string, unknown>;
  return Number.isSafeInteger(source.version) && (source.version as number) > 0 && "value" in source ? { version: source.version as number, value: source.value } : undefined;
}

function useSystemColorScheme(): ColorScheme {
  return useSyncExternalStore(
    (listener) => {
      if (typeof window === "undefined") return () => undefined;
      const query = window.matchMedia?.("(prefers-color-scheme: dark)");
      query?.addEventListener("change", listener);
      return () => query?.removeEventListener("change", listener);
    },
    () => typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
    () => "light",
  );
}
