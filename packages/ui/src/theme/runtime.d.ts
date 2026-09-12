export type ColorSchemePreference = "light" | "dark" | "system";
export type ColorScheme = Exclude<ColorSchemePreference, "system">;
export type ThemeTokenName =
  | "--surface-canvas" | "--surface-sheet" | "--surface-sheet-2" | "--surface-rail" | "--surface-rail-hi"
  | "--surface-popover" | "--surface-inset" | "--text-primary" | "--text-secondary" | "--text-muted"
  | "--text-subtle" | "--text-inverse" | "--text-on-rail" | "--text-on-rail-mut" | "--text-on-rail-hi"
  | "--text-on-brand" | "--text-link" | "--border-subtle" | "--border-default" | "--border-strong"
  | "--border-focus" | "--border-on-rail" | "--brand" | "--brand-hover" | "--brand-active" | "--brand-soft"
  | "--brand-soft-text" | "--accent" | "--accent-soft" | "--accent-soft-text" | "--success-soft" | "--success-text"
  | "--warning-soft" | "--warning-text" | "--danger-soft" | "--danger-text" | "--info-soft" | "--info-text"
  | "--success" | "--on-success" | "--success-line" | "--success-tint"
  | "--warning" | "--on-warning" | "--warning-line" | "--warning-tint"
  | "--danger" | "--danger-hover" | "--danger-active" | "--on-danger" | "--danger-line" | "--danger-tint"
  | "--info" | "--on-info" | "--info-line" | "--info-tint"
  | "--on-accent" | "--accent-line" | "--accent-tint" | "--brand-line" | "--brand-tint"
  | "--ring" | "--ring-danger" | "--font-family-sans" | "--font-family-mono" | "--elevation-xs" | "--elevation-sm"
  | "--elevation-md" | "--elevation-lg" | "--elevation-popover" | "--r-2" | "--r-4" | "--r-6" | "--r-8"
  | "--r-10" | "--r-12" | "--r-full" | "--rail-w" | "--topbar-h" | "--controlpanel-h" | "--chatter-w"
  | "--control-h-sm" | "--control-h-md" | "--control-h-lg";
export type TokenLayer = Partial<Record<ThemeTokenName, string>>;
export interface ThemeTokenLayers { shared: TokenLayer; light: TokenLayer; dark: TokenLayer }
export interface ThemeOptionsEnvelope { version: number; value: unknown }
export type ThemeOptionsCapability = "palette-customization";
export interface ThemeOptionsDefinition<TOptions> { version: number; defaults: TOptions; capability?: ThemeOptionsCapability; parse(value: unknown): TOptions; migrate?(value: unknown, fromVersion: number): TOptions; resolve(value: TOptions): Partial<ThemeTokenLayers> }
export type ThemeCustomizationFont = "theme" | "system" | "inter" | "humanist" | "industrial" | "editorial" | "mono";
export type ThemeCustomizationRadius = "theme" | "square" | "compact" | "standard" | "soft" | "round";
export type ThemeCustomizationDensity = "theme" | "compact" | "balanced" | "comfortable" | "spacious";
export type ThemeCustomizationElevation = "theme" | "flat" | "subtle" | "soft" | "dramatic";
export type ThemeCustomizationLogo = "theme" | "brand" | "accent" | "mono" | "star" | "corner";
export interface ThemeCustomization {
  brand: string;
  accent: string;
  neutral: string;
  canvas: string;
  surface: string;
  rail: string;
  success: string;
  warning: string;
  danger: string;
  info: string;
  font: ThemeCustomizationFont;
  radius: ThemeCustomizationRadius;
  density: ThemeCustomizationDensity;
  elevation: ThemeCustomizationElevation;
  logo: ThemeCustomizationLogo;
}
export interface ThemeCustomizationConfiguration {
  version?: number;
  migrate?(value: unknown, fromVersion: number, defaults: ThemeCustomization): ThemeCustomization;
}
export interface ThemeDefinition<TOptions = Record<string, never>> { contractVersion: 1; id: string; legacyIds?: readonly string[]; labelKey: string; descriptionKey: string; revision: number; tokens: ThemeTokenLayers; stylesheets?: readonly string[]; options?: ThemeOptionsDefinition<TOptions> }
export interface ResolvedThemeOptions<TOptions = unknown> { version: number; value: TOptions; tokens: ThemeTokenLayers }
export interface SerializableThemeMetadata { contractVersion: 1; id: string; legacyIds: readonly string[]; labelKey: string; descriptionKey: string; revision: number; optionsVersion: number | null; optionDefaults: unknown; optionsCapability: ThemeOptionsCapability | null }
export const THEME_CONTRACT_VERSION: 1;
export const THEME_ID_PATTERN: RegExp;
export const THEME_TOKEN_NAMES: readonly ThemeTokenName[];
export function defineTheme<TOptions>(definition: ThemeDefinition<TOptions>): ThemeDefinition<TOptions>;
export function assertThemeDefinition(value: unknown): ThemeDefinition<unknown>;
export function assertThemeCatalogue(definitions: readonly ThemeDefinition<unknown>[]): ThemeDefinition<unknown>[];
export function resolveThemeOptions<TOptions>(definition: ThemeDefinition<TOptions>, envelope?: ThemeOptionsEnvelope | null): ResolvedThemeOptions<TOptions>;
export function createThemeCustomizationOptions(defaults: ThemeCustomization, configuration?: ThemeCustomizationConfiguration): ThemeOptionsDefinition<ThemeCustomization>;
export function isThemeCustomizationOptions(options: ThemeOptionsDefinition<unknown> | undefined): options is ThemeOptionsDefinition<ThemeCustomization> & { capability: "palette-customization" };
export function migrateThemeCustomization(value: unknown, defaults: ThemeCustomization): ThemeCustomization;
export function migrateThemeCustomizationFromV1(value: unknown, fromVersion: number, defaults: ThemeCustomization): ThemeCustomization;
export function parseThemeCustomization(value: unknown): ThemeCustomization;
export function compileThemeCss(definitions: readonly ThemeDefinition<unknown>[]): string;
export function serializableThemeMetadata(definition: ThemeDefinition<unknown>): SerializableThemeMetadata;
