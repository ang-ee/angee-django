import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { stableKey } from "@angee/refine";
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
  /** Saved preferences, independent of any open draft. */
  preferences: AppearancePreferences;
  /** Preferences being applied: the open draft, or the saved preferences. */
  currentPreferences: AppearancePreferences;
  dirty: boolean;
  effectiveThemeId: string | null;
  effectiveColorSchemePreference: ColorSchemePreference;
  colorScheme: ColorScheme;
  theme: ThemeContribution | null;
  hostTheme: ThemeContribution | null;
  hostOptions?: ThemeOptionsEnvelope;
  effectiveOptions?: ThemeOptionsEnvelope;
  available: boolean;
  editable: boolean;
  resolvingIdentity: boolean;
  saving: boolean;
  error: Error | null;
  notice: "theme-unavailable" | "options-invalid" | "version-unsupported" | null;
  setTheme: (themeId: string | undefined, options?: ThemeOptionsEnvelope) => Promise<void>;
  setColorScheme: (preference: ColorSchemePreference | undefined) => Promise<void>;
  setOptions: (options: ThemeOptionsEnvelope) => Promise<void>;
  openDraft: () => void;
  save: () => Promise<void>;
  discard: () => void;
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
  const catalogue = useMemo(() => {
    const entries = new Map<string, ThemeContribution>();
    for (const theme of runtime.themes) {
      entries.set(theme.definition.id, theme);
      for (const legacyId of theme.definition.legacyIds ?? []) entries.set(legacyId, theme);
    }
    return entries;
  }, [runtime.themes]);
  const saved = readAppearancePreferences(userPreferences.preferences);
  const savedTheme = saved.value.themeId ? catalogue.get(saved.value.themeId) : undefined;
  const preferences = savedTheme ? { ...saved.value, themeId: savedTheme.definition.id } : saved.value;
  const [draft, setDraftState] = useState<AppearancePreferences | null>(null);
  // Commands share the latest draft even when React batches several edits.
  const draftRef = useRef(draft);
  const setDraft = useCallback((next: AppearancePreferences | null) => {
    draftRef.current = next;
    setDraftState(next);
  }, []);
  const currentPreferences = draft ?? preferences;
  const dirty = draft !== null && stableKey(draft) !== stableKey(preferences);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [pendingLegacy, setPendingLegacy] = useState<PendingLegacyColorScheme | null>(readPendingLegacyColorScheme);
  const system = useSystemColorScheme();
  const hostThemeId = host.themeId ?? null;
  const hostTheme = hostThemeId ? catalogue.get(hostThemeId) ?? null : null;
  const savedAppearance = resolveAppearance(preferences, saved.notice, catalogue, host, system);
  const { effectiveThemeId, effectiveTheme, colorSchemePreference, colorScheme, effectiveTokens, effectiveOptions, notice } =
    draft ? resolveAppearance(draft, null, catalogue, host, system) : savedAppearance;
  let hostOptions: ThemeOptionsEnvelope | undefined;
  if (hostTheme?.definition.options) {
    try {
      const resolved = resolveThemeOptions(hostTheme.definition, host.options);
      hostOptions = { version: resolved.version, value: resolved.value };
    } catch {
      const resolved = resolveThemeOptions(hostTheme.definition);
      hostOptions = { version: resolved.version, value: resolved.value };
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
      themeId: savedAppearance.effectiveThemeId,
      colorSchemePreference: savedAppearance.colorSchemePreference,
      colorScheme: savedAppearance.colorScheme,
      options: savedAppearance.effectiveOptions,
      tokens: savedAppearance.effectiveTokens,
      ...(pendingLegacy ? { pendingLegacy } : {}),
    });
  }, [auth.user?.id, savedAppearance, host.fingerprint, pendingLegacy, resolvingIdentity]);

  const update = useCallback(async (
    apply: (current: AppearancePreferences) => AppearancePreferences | undefined,
    operation: "edit" | "scheme" | "theme" | "save" | "reset" = "edit",
  ) => {
    if (!userPreferences.available) return;
    const editingDraft = operation !== "save" && operation !== "reset" && draftRef.current !== null;
    if (!editingDraft) setSaving(true);
    setError(null);
    try {
      if (editingDraft && draftRef.current) {
        setDraft(apply(draftRef.current) ?? null);
        return;
      }
      await userPreferences.patchPreferences((current) => {
        const decoded = readAppearancePreferences(current);
        if (!decoded.writable && operation !== "reset") {
          throw new Error("This appearance preference was written by a newer version. Reset it before editing.");
        }
        let next: AppearancePreferences | (Omit<AppearancePreferences, "options"> & { options?: unknown }) | undefined = apply(decoded.value);
        if (operation === "scheme" && next && decoded.hasRawOptions && next.options === undefined) {
          next = { ...next, options: decoded.rawOptions };
        }
        if (next?.themeId) {
          const canonical = catalogue.get(next.themeId)?.definition.id;
          if (canonical) next = { ...next, themeId: canonical };
        }
        return writeAppearancePreferences(current, next);
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)));
      throw caught;
    } finally {
      if (!editingDraft) setSaving(false);
    }
  }, [catalogue, setDraft, userPreferences]);

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
        themeId: definition.id,
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
    await update((current) => {
      const themeId = current.themeId ?? hostTheme?.definition.id;
      if (!themeId) throw new Error("Choose an installed theme before changing its options.");
      const definition = catalogue.get(themeId)?.definition;
      if (!definition?.options) throw new Error(`Theme ${JSON.stringify(themeId)} does not accept options.`);
      const resolved = resolveThemeOptions(definition, options);
      return { ...current, themeId: definition.id, options: { version: resolved.version, value: resolved.value } };
    });
  }, [catalogue, hostTheme, update]);
  const openDraft = useCallback(() => {
    if (!userPreferences.available || !saved.writable || resolvingIdentity || draftRef.current !== null) return;
    setError(null);
    setDraft(preferences);
  }, [preferences, resolvingIdentity, saved.writable, setDraft, userPreferences.available]);
  const save = useCallback(async () => {
    const pending = draftRef.current;
    if (!pending || !userPreferences.available || resolvingIdentity) return;
    await update(() => pending, "save");
    setDraft(null);
  }, [resolvingIdentity, setDraft, update, userPreferences.available]);
  const discard = useCallback(() => {
    setDraft(null);
    setError(null);
  }, [setDraft]);
  const reset = useCallback(async () => {
    setDraft(null);
    await update(() => undefined, "reset");
  }, [setDraft, update]);

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
    if (draft || !pendingLegacy || resolvingIdentity || auth.status !== "authenticated" || !auth.user?.id || !userPreferences.available) return;
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
  }, [auth.status, auth.user?.id, draft, pendingLegacy, resolvingIdentity, saved.value.colorScheme, saved.writable, setColorScheme, userPreferences.available]);

  const value = useMemo<AppearanceState>(() => ({
    preferences,
    currentPreferences,
    dirty,
    effectiveThemeId,
    effectiveColorSchemePreference: colorSchemePreference,
    colorScheme,
    theme: effectiveTheme,
    hostTheme,
    hostOptions,
    effectiveOptions,
    available: userPreferences.available,
    editable: saved.writable,
    resolvingIdentity,
    saving,
    error,
    notice,
    setTheme,
    setColorScheme,
    setOptions,
    openDraft,
    save,
    discard,
    reset,
  }), [preferences, currentPreferences, dirty, effectiveThemeId, colorSchemePreference, colorScheme, effectiveTheme, hostTheme, hostOptions, effectiveOptions, userPreferences.available, saved.writable, resolvingIdentity, saving, error, notice, setTheme, setColorScheme, setOptions, openDraft, save, discard, reset]);
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

/** Resolve both live appearance and the saved-only boot cache through one path. */
function resolveAppearance(preferences: AppearancePreferences, notice: AppearanceState["notice"], catalogue: ReadonlyMap<string, ThemeContribution>, host: HostAppearanceDefaults, system: ColorScheme) {
  const hostTheme = host.themeId ? catalogue.get(host.themeId) : undefined;
  const selectedThemeId = preferences.themeId ?? host.themeId;
  const selectedTheme = selectedThemeId ? catalogue.get(selectedThemeId) : undefined;
  if (selectedThemeId && !selectedTheme) notice ??= "theme-unavailable";
  const effectiveTheme = selectedTheme ?? hostTheme ?? null;
  const effectiveThemeId = effectiveTheme?.definition.id ?? null;
  const colorSchemePreference = preferences.colorScheme ?? host.colorScheme ?? "system";
  const colorScheme = colorSchemePreference === "system" ? system : colorSchemePreference;
  const requestedOptions = preferences.themeId === undefined ? host.options : preferences.options;
  let effectiveTokens: Partial<Record<ThemeTokenName, string>> = {};
  let effectiveOptions: ThemeOptionsEnvelope | undefined;
  if (effectiveTheme) {
    let resolved;
    try {
      resolved = resolveThemeOptions(effectiveTheme.definition, requestedOptions);
    } catch {
      notice ??= "options-invalid";
      resolved = resolveThemeOptions(effectiveTheme.definition);
    }
    effectiveTokens = { ...resolved.tokens.shared, ...resolved.tokens[colorScheme] };
    if (effectiveTheme.definition.options) effectiveOptions = { version: resolved.version, value: resolved.value };
  }
  return { effectiveThemeId, effectiveTheme, colorSchemePreference, colorScheme, effectiveTokens, effectiveOptions, notice };
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

/** Read the actor bound to a current, bounded appearance bootstrap cache entry. */
export function appearanceCacheActorId(encoded: string | null): string | null {
  if (!encoded || encoded.length > APPEARANCE_CACHE_LIMIT) return null;
  try {
    const value = JSON.parse(encoded) as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const source = value as Record<string, unknown>;
    return source.schema === APPEARANCE_CACHE_SCHEMA && typeof source.actorId === "string"
      ? source.actorId
      : null;
  } catch {
    return null;
  }
}

function writeAppearanceCache(value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    const encoded = JSON.stringify(value);
    if (
      encoded.length <= APPEARANCE_CACHE_LIMIT
      && window.localStorage.getItem(APPEARANCE_CACHE_KEY) !== encoded
    ) window.localStorage.setItem(APPEARANCE_CACHE_KEY, encoded);
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
