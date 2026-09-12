/** @deprecated Use the color-scheme APIs. Theme now names an installed design. */
export {
  COLOR_SCHEME_STORAGE_KEY as THEME_STORAGE_KEY,
  applyColorSchemePreference as applyThemePreference,
  normaliseColorSchemePreference as normaliseThemePreference,
  storedColorSchemePreference as storedThemePreference,
  setColorSchemePreference as setThemePreference,
  resolveColorSchemePreference as resolvedThemePreference,
  systemColorScheme as systemThemePreference,
  useColorSchemePreference as useThemePreference,
} from "./color-scheme";
export type {
  ColorSchemePreference as ThemePreference,
  ColorScheme as ResolvedTheme,
  ColorSchemeState as ThemeState,
} from "./color-scheme";
