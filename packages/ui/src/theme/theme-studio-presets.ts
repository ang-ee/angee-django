/**
 * Theme Studio — standalone preset definitions.
 *
 * These presets extend the existing ThemeCustomization vocabulary (font,
 * radius, density, elevation) with two new dimensions — borders and input
 * label style — without touching runtime.mjs or any of the seven existing
 * themes. ThemeStudio applies them directly as inline CSS custom properties
 * on the preview container, so they are completely isolated from the
 * app-level AppearanceProvider.
 *
 * Naming follows the existing pattern: NOUN_PRESETS / NOUN_KEYS.
 */

// ─── Border presets ───────────────────────────────────────────────────────────
// Each preset overrides the three semantic border tokens that components
// reference. Values use the same primitive-style notation as tokens.css.

export type BorderPreset = "borderless" | "subtle" | "default" | "strong";

export const BORDER_PRESET_LABELS: Record<BorderPreset, string> = {
  borderless: "Borderless",
  subtle:     "Subtle",
  default:    "Default",
  strong:     "Strong",
};

/** CSS vars to inject into the preview container per border preset.
 *  Only the three tokens that form the shared border vocabulary. */
export const BORDER_PRESET_TOKENS: Record<
  BorderPreset,
  { "--border-subtle": string; "--border-default": string; "--border-strong": string }
> = {
  borderless: {
    "--border-subtle":  "transparent",
    "--border-default": "transparent",
    "--border-strong":  "color-mix(in oklab, currentColor 12%, transparent)",
  },
  subtle: {
    "--border-subtle":  "color-mix(in oklab, currentColor 6%, transparent)",
    "--border-default": "color-mix(in oklab, currentColor 10%, transparent)",
    "--border-strong":  "color-mix(in oklab, currentColor 18%, transparent)",
  },
  default: {
    // "default" means: use whatever tokens.css / the active theme already
    // defines — emit nothing so the cascade is undisturbed.
    "--border-subtle":  "",
    "--border-default": "",
    "--border-strong":  "",
  },
  strong: {
    "--border-subtle":  "color-mix(in oklab, currentColor 16%, transparent)",
    "--border-default": "color-mix(in oklab, currentColor 24%, transparent)",
    "--border-strong":  "color-mix(in oklab, currentColor 36%, transparent)",
  },
};

// ─── Input label style ────────────────────────────────────────────────────────

export type InputLabelStyle = "stacked" | "floating";

export const INPUT_LABEL_STYLE_LABELS: Record<InputLabelStyle, string> = {
  stacked:  "Stacked",
  floating: "Floating",
};

// ─── ThemeStudio state shape ──────────────────────────────────────────────────
// The complete state managed by ThemeStudio. ThemeCustomization fields come
// from the existing runtime contract; the two new dimensions are additive.

import type { ThemeCustomization } from "./runtime.mjs";

export interface ThemeStudioState extends ThemeCustomization {
  /** Active theme id from the runtime catalogue, e.g. "angee.stock". */
  themeId: string;
  /** Light vs dark canvas for the preview. */
  colorScheme: "light" | "dark";
  /** Border visual weight preset (ThemeStudio-only, not in runtime.mjs). */
  borders: BorderPreset;
  /** Input label animation style (ThemeStudio-only). */
  inputLabelStyle: InputLabelStyle;
}

// ─── Default state ────────────────────────────────────────────────────────────

export const THEME_STUDIO_DEFAULTS: ThemeStudioState = {
  // ThemeCustomization defaults mirror angee.stock:
  themeId: "angee.stock",
  colorScheme: "light",
  brand:   "#5b5bd6",
  accent:  "#0d9488",
  neutral: "#6b7280",
  canvas:  "#f7f8fa",
  surface: "#ffffff",
  rail:    "#0a0c10",
  success: "#10b981",
  warning: "#f59e0b",
  danger:  "#ef4444",
  info:    "#3b82f6",
  font:      "theme",
  radius:    "theme",
  density:   "theme",
  elevation: "theme",
  logo:      "theme",
  // New:
  borders:        "default",
  inputLabelStyle: "stacked",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Build the inline style object for the preview container. Only emits values
 *  that differ from "default" / "theme" so the active AppearanceProvider
 *  cascade wins for everything the user hasn't explicitly overridden. */
export function buildPreviewOverrides(
  state: ThemeStudioState,
): Record<string, string> {
  const vars: Record<string, string> = {};

  // Border preset — apply only when non-default.
  if (state.borders !== "default") {
    const tokens = BORDER_PRESET_TOKENS[state.borders];
    for (const [key, val] of Object.entries(tokens)) {
      if (val) vars[key] = val;
    }
  }

  return vars;
}

// ─── Preset option lists (for UI rendering) ───────────────────────────────────

export const BORDER_PRESETS: readonly BorderPreset[] = [
  "borderless", "subtle", "default", "strong",
];

export const INPUT_LABEL_STYLES: readonly InputLabelStyle[] = [
  "stacked", "floating",
];

// Re-export existing preset keys for ThemeStudio UI pickers.
export const RADIUS_PRESETS = ["square", "compact", "standard", "soft", "round"] as const;
export const DENSITY_PRESETS = ["compact", "balanced", "comfortable", "spacious"] as const;
export const ELEVATION_PRESETS = ["flat", "subtle", "soft", "dramatic"] as const;
export const FONT_PRESETS = ["system", "inter", "humanist", "industrial", "editorial", "mono"] as const;

export const RADIUS_LABELS: Record<string, string> = {
  theme:    "Theme default",
  square:   "Square",
  compact:  "Compact",
  standard: "Standard",
  soft:     "Soft",
  round:    "Round",
};

export const DENSITY_LABELS: Record<string, string> = {
  theme:       "Theme default",
  compact:     "Compact",
  balanced:    "Balanced",
  comfortable: "Comfortable",
  spacious:    "Spacious",
};

export const ELEVATION_LABELS: Record<string, string> = {
  theme:    "Theme default",
  flat:     "Flat",
  subtle:   "Subtle",
  soft:     "Soft",
  dramatic: "Dramatic",
};

export const FONT_LABELS: Record<string, string> = {
  theme:      "Theme default",
  system:     "System UI",
  inter:      "Inter",
  humanist:   "Humanist",
  industrial: "Industrial",
  editorial:  "Editorial",
  mono:       "Monospace",
};
