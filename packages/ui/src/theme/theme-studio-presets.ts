/**
 * Theme Studio — standalone preset definitions.
 *
 * These presets extend the existing ThemeCustomization vocabulary (font,
 * radius, density, elevation) with additional dimensions — borders, input
 * label style, google font — without touching runtime.mjs or any of the
 * seven existing themes. ThemeStudio applies them directly as inline CSS
 * custom properties on the preview container.
 *
 * Naming follows the existing pattern: NOUN_PRESETS / NOUN_KEYS.
 */

import type { ThemeCustomization } from "./runtime.mjs";

// ─── Border presets ───────────────────────────────────────────────────────────
// Overrides all three semantic border token vars + the color bridge vars
// so ALL components (buttons, cards, inputs, chips…) respond.

export type BorderPreset = "borderless" | "subtle" | "default" | "strong";

export const BORDER_PRESET_LABELS: Record<BorderPreset, string> = {
  borderless: "Borderless",
  subtle:     "Subtle",
  default:    "Default",
  strong:     "Strong",
};

/**
 * CSS vars to inject per border preset.
 * Emits BOTH the Angee semantic tokens (--border-*) AND the Tailwind bridge
 * vars (--color-border-*) so all border-* utilities update inside the
 * preview container.
 *
 * Buttons that use border-brand / border-danger are intentionally left
 * alone — those are semantic fills, not structural borders.
 * The "borderless" preset makes ghost-style structure invisible while keeping
 * brand-colored action buttons intact.
 */
type BorderTokenSet = Record<string, string>;

export const BORDER_PRESET_TOKENS: Record<BorderPreset, BorderTokenSet> = {
  borderless: {
    // Semantic tokens
    "--border-subtle":  "transparent",
    "--border-default": "transparent",
    "--border-strong":  "transparent",
    // Tailwind bridge vars (used by border-border-subtle, border-border, border-border-strong)
    "--color-border-subtle": "transparent",
    "--color-border":        "transparent",
    "--color-border-strong": "transparent",
    // Secondary button uses border-border-strong — make it visible via bg contrast only
    "--color-input": "transparent",
  },
  subtle: {
    "--border-subtle":  "color-mix(in oklab, currentColor 6%, transparent)",
    "--border-default": "color-mix(in oklab, currentColor 10%, transparent)",
    "--border-strong":  "color-mix(in oklab, currentColor 16%, transparent)",
    "--color-border-subtle": "color-mix(in oklab, currentColor 6%, transparent)",
    "--color-border":        "color-mix(in oklab, currentColor 10%, transparent)",
    "--color-border-strong": "color-mix(in oklab, currentColor 16%, transparent)",
    "--color-input":         "color-mix(in oklab, currentColor 10%, transparent)",
  },
  default: {
    // No override — let the active theme cascade win entirely.
  },
  strong: {
    "--border-subtle":  "color-mix(in oklab, currentColor 16%, transparent)",
    "--border-default": "color-mix(in oklab, currentColor 24%, transparent)",
    "--border-strong":  "color-mix(in oklab, currentColor 36%, transparent)",
    "--color-border-subtle": "color-mix(in oklab, currentColor 16%, transparent)",
    "--color-border":        "color-mix(in oklab, currentColor 24%, transparent)",
    "--color-border-strong": "color-mix(in oklab, currentColor 36%, transparent)",
    "--color-input":         "color-mix(in oklab, currentColor 24%, transparent)",
  },
};

// ─── Elevation presets ────────────────────────────────────────────────────────
// Exact shadow values from runtime.mjs ELEVATION_TOKENS.
// We need to emit BOTH the Angee --elevation-* tokens AND the Tailwind
// --shadow-* bridge vars so shadow-xs/sm/md/lg/popover utilities respond
// inside the preview container.

type ElevationSet = {
  light: Record<string, string>;
  dark:  Record<string, string>;
};

const SHADOW_NAMES = [
  "--elevation-xs",
  "--elevation-sm",
  "--elevation-md",
  "--elevation-lg",
  "--elevation-popover",
] as const;

const SHADOW_BRIDGE_NAMES = [
  "--shadow-xs",
  "--shadow-sm",
  "--shadow-md",
  "--shadow-lg",
  "--shadow-popover",
] as const;

export const ELEVATION_PRESET_TOKENS: Record<string, ElevationSet> = {
  // ── shadowless — zero shadows everywhere ────────────────────────────────────
  shadowless: {
    light: {
      "--elevation-xs":      "none",
      "--elevation-sm":      "none",
      "--elevation-md":      "none",
      "--elevation-lg":      "none",
      "--elevation-popover": "none",
      "--shadow-xs":         "none",
      "--shadow-sm":         "none",
      "--shadow-md":         "none",
      "--shadow-lg":         "none",
      "--shadow-popover":    "none",
    },
    dark: {
      "--elevation-xs":      "none",
      "--elevation-sm":      "none",
      "--elevation-md":      "none",
      "--elevation-lg":      "none",
      "--elevation-popover": "none",
      "--shadow-xs":         "none",
      "--shadow-sm":         "none",
      "--shadow-md":         "none",
      "--shadow-lg":         "none",
      "--shadow-popover":    "none",
    },
  },

  // ── flat — border-only, no blur ─────────────────────────────────────────────
  flat: {
    light: {
      "--elevation-xs":      "0 0 0 1px #0000001a",
      "--elevation-sm":      "0 0 0 1px #00000026",
      "--elevation-md":      "0 0 0 1px #00000033",
      "--elevation-lg":      "0 0 0 1px #00000040",
      "--elevation-popover": "0 0 0 1px #0000004d",
      "--shadow-xs":         "0 0 0 1px #0000001a",
      "--shadow-sm":         "0 0 0 1px #00000026",
      "--shadow-md":         "0 0 0 1px #00000033",
      "--shadow-lg":         "0 0 0 1px #00000040",
      "--shadow-popover":    "0 0 0 1px #0000004d",
    },
    dark: {
      "--elevation-xs":      "0 0 0 1px #ffffff1a",
      "--elevation-sm":      "0 0 0 1px #ffffff26",
      "--elevation-md":      "0 0 0 1px #ffffff33",
      "--elevation-lg":      "0 0 0 1px #ffffff40",
      "--elevation-popover": "0 0 0 1px #ffffff4d",
      "--shadow-xs":         "0 0 0 1px #ffffff1a",
      "--shadow-sm":         "0 0 0 1px #ffffff26",
      "--shadow-md":         "0 0 0 1px #ffffff33",
      "--shadow-lg":         "0 0 0 1px #ffffff40",
      "--shadow-popover":    "0 0 0 1px #ffffff4d",
    },
  },

  // ── subtle — whisper-thin, barely-there ─────────────────────────────────────
  subtle: {
    light: {
      "--elevation-xs":      "0 1px 3px #00000018, 0 1px 1px #0000000f",
      "--elevation-sm":      "0 2px 6px #00000020, 0 1px 2px #00000014",
      "--elevation-md":      "0 6px 16px #00000028, 0 2px 4px #0000001a",
      "--elevation-lg":      "0 16px 40px #00000033, 0 4px 8px #00000020",
      "--elevation-popover": "0 8px 24px #0000002e, 0 2px 6px #0000001a",
      "--shadow-xs":         "0 1px 3px #00000018, 0 1px 1px #0000000f",
      "--shadow-sm":         "0 2px 6px #00000020, 0 1px 2px #00000014",
      "--shadow-md":         "0 6px 16px #00000028, 0 2px 4px #0000001a",
      "--shadow-lg":         "0 16px 40px #00000033, 0 4px 8px #00000020",
      "--shadow-popover":    "0 8px 24px #0000002e, 0 2px 6px #0000001a",
    },
    dark: {
      "--elevation-xs":      "0 1px 3px #00000066, 0 1px 1px #00000040",
      "--elevation-sm":      "0 2px 6px #00000080, 0 1px 2px #00000060",
      "--elevation-md":      "0 6px 16px #00000099, 0 2px 4px #00000073",
      "--elevation-lg":      "0 16px 40px #000000b3, 0 4px 8px #00000080",
      "--elevation-popover": "0 8px 24px #000000a0, 0 2px 6px #00000073",
      "--shadow-xs":         "0 1px 3px #00000066, 0 1px 1px #00000040",
      "--shadow-sm":         "0 2px 6px #00000080, 0 1px 2px #00000060",
      "--shadow-md":         "0 6px 16px #00000099, 0 2px 4px #00000073",
      "--shadow-lg":         "0 16px 40px #000000b3, 0 4px 8px #00000080",
      "--shadow-popover":    "0 8px 24px #000000a0, 0 2px 6px #00000073",
    },
  },

  // ── soft — wide diffuse shadows, clearly visible ─────────────────────────────
  soft: {
    light: {
      "--elevation-xs":      "0 2px 8px #0000002a, 0 1px 2px #0000001a",
      "--elevation-sm":      "0 4px 16px #00000035, 0 2px 4px #00000022",
      "--elevation-md":      "0 10px 32px #00000042, 0 3px 8px #0000002a",
      "--elevation-lg":      "0 24px 60px #00000052, 0 6px 16px #00000038",
      "--elevation-popover": "0 14px 44px #0000004a, 0 4px 10px #00000030",
      "--shadow-xs":         "0 2px 8px #0000002a, 0 1px 2px #0000001a",
      "--shadow-sm":         "0 4px 16px #00000035, 0 2px 4px #00000022",
      "--shadow-md":         "0 10px 32px #00000042, 0 3px 8px #0000002a",
      "--shadow-lg":         "0 24px 60px #00000052, 0 6px 16px #00000038",
      "--shadow-popover":    "0 14px 44px #0000004a, 0 4px 10px #00000030",
    },
    dark: {
      "--elevation-xs":      "0 2px 8px #00000080, 0 1px 2px #00000060",
      "--elevation-sm":      "0 4px 16px #00000099, 0 2px 4px #00000073",
      "--elevation-md":      "0 10px 32px #000000b3, 0 3px 8px #00000090",
      "--elevation-lg":      "0 24px 60px #000000cc, 0 6px 16px #000000a0",
      "--elevation-popover": "0 14px 44px #000000c0, 0 4px 10px #00000090",
      "--shadow-xs":         "0 2px 8px #00000080, 0 1px 2px #00000060",
      "--shadow-sm":         "0 4px 16px #00000099, 0 2px 4px #00000073",
      "--shadow-md":         "0 10px 32px #000000b3, 0 3px 8px #00000090",
      "--shadow-lg":         "0 24px 60px #000000cc, 0 6px 16px #000000a0",
      "--shadow-popover":    "0 14px 44px #000000c0, 0 4px 10px #00000090",
    },
  },

  // ── dramatic — heavy, expressive, unmissable ──────────────────────────────────
  dramatic: {
    light: {
      "--elevation-xs":      "0 4px 14px #00000040, 0 1px 4px #0000002a",
      "--elevation-sm":      "0 8px 24px #00000055, 0 3px 8px #00000038",
      "--elevation-md":      "0 18px 48px #00000066, 0 6px 16px #00000048",
      "--elevation-lg":      "0 36px 80px #00000080, 0 12px 28px #00000060",
      "--elevation-popover": "0 24px 60px #00000070, 0 8px 20px #00000055",
      "--shadow-xs":         "0 4px 14px #00000040, 0 1px 4px #0000002a",
      "--shadow-sm":         "0 8px 24px #00000055, 0 3px 8px #00000038",
      "--shadow-md":         "0 18px 48px #00000066, 0 6px 16px #00000048",
      "--shadow-lg":         "0 36px 80px #00000080, 0 12px 28px #00000060",
      "--shadow-popover":    "0 24px 60px #00000070, 0 8px 20px #00000055",
    },
    dark: {
      "--elevation-xs":      "0 4px 14px #000000b3, 0 1px 4px #00000090",
      "--elevation-sm":      "0 8px 24px #000000cc, 0 3px 8px #000000a0",
      "--elevation-md":      "0 18px 48px #000000e0, 0 6px 16px #000000c0",
      "--elevation-lg":      "0 36px 80px #000000f0, 0 12px 28px #000000d0",
      "--elevation-popover": "0 24px 60px #000000e8, 0 8px 20px #000000c0",
      "--shadow-xs":         "0 4px 14px #000000b3, 0 1px 4px #00000090",
      "--shadow-sm":         "0 8px 24px #000000cc, 0 3px 8px #000000a0",
      "--shadow-md":         "0 18px 48px #000000e0, 0 6px 16px #000000c0",
      "--shadow-lg":         "0 36px 80px #000000f0, 0 12px 28px #000000d0",
      "--shadow-popover":    "0 24px 60px #000000e8, 0 8px 20px #000000c0",
    },
  },
};

// Export these for other modules that may need them
export { SHADOW_NAMES, SHADOW_BRIDGE_NAMES };

// ─── Input label style ────────────────────────────────────────────────────────

export type InputLabelStyle = "stacked" | "inline";

export const INPUT_LABEL_STYLE_LABELS: Record<InputLabelStyle, string> = {
  stacked: "Stacked",
  inline:  "Inline",
};

// Google Font options now live in google-fonts-list.ts

// ─── ThemeStudio state shape ──────────────────────────────────────────────────

export type StudioElevation =
  | "theme" | "shadowless" | "flat" | "subtle" | "soft" | "dramatic";

// ─── Motion presets ───────────────────────────────────────────────────────────

export const MOTION_PRESETS = ["none", "snappy", "smooth", "relaxed", "springy"] as const;
export type MotionPreset = typeof MOTION_PRESETS[number];

export const MOTION_PRESET_LABELS: Record<MotionPreset, string> = {
  none:    "None",
  snappy:  "Snappy",
  smooth:  "Smooth",
  relaxed: "Relaxed",
  springy: "Springy",
};

/** CSS vars each motion preset sets on the preview container. */
export const MOTION_PRESET_VARS: Record<MotionPreset, Record<string, string>> = {
  none: {
    "--dur-fast": "0ms",
    "--dur-base": "0ms",
    "--dur-slow": "0ms",
    "--ease-ui":  "linear",
    "--motion-speed-mult": "0",
  },
  snappy: {
    "--dur-fast": "60ms",
    "--dur-base": "100ms",
    "--dur-slow": "160ms",
    "--ease-ui":  "cubic-bezier(0.2, 0, 0, 1)",
    "--motion-speed-mult": "0.6",
  },
  smooth: {
    "--dur-fast": "120ms",
    "--dur-base": "180ms",
    "--dur-slow": "280ms",
    "--ease-ui":  "cubic-bezier(0.2, 0.6, 0.2, 1)",
    "--motion-speed-mult": "1",
  },
  relaxed: {
    "--dur-fast": "180ms",
    "--dur-base": "280ms",
    "--dur-slow": "420ms",
    "--ease-ui":  "cubic-bezier(0.4, 0, 0.2, 1)",
    "--motion-speed-mult": "1.5",
  },
  springy: {
    "--dur-fast": "140ms",
    "--dur-base": "220ms",
    "--dur-slow": "340ms",
    "--ease-ui":  "cubic-bezier(0.34, 1.56, 0.64, 1)",
    "--motion-speed-mult": "1.2",
  },
};

export interface ThemeStudioState extends Omit<ThemeCustomization, "elevation"> {
  themeId: string;
  colorScheme: "light" | "dark";
  borders: BorderPreset;
  inputLabelStyle: InputLabelStyle;
  /** A Google Fonts family name (e.g. "Outfit") or null for system/preset fonts. */
  googleFont: string | null;
  /** Elevation preset — extends ThemeCustomization with "shadowless" (preview-only). */
  elevation: StudioElevation;
  /** Motion preset controlling transition speed and easing. */
  motionPreset: MotionPreset;
  /** Speed multiplier override (0.25–3). When not "theme", overrides the preset's mult. */
  motionSpeed: number;
}

// ─── Default state ────────────────────────────────────────────────────────────

export const THEME_STUDIO_DEFAULTS: ThemeStudioState = {
  themeId:    "angee.stock",
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
  borders:         "default",
  inputLabelStyle: "stacked",
  googleFont:      null,
  motionPreset:    "smooth",
  motionSpeed:     1,
};

// ─── buildPreviewOverrides ────────────────────────────────────────────────────

/** Build the inline style object for the preview container.
 *  Handles border and elevation overrides that live outside the main
 *  buildThemeVars function (which lives in ThemeStudio.tsx). */
export function buildPreviewOverrides(
  state: ThemeStudioState,
): Record<string, string> {
  const vars: Record<string, string> = {};

  // Border preset
  if (state.borders !== "default") {
    const tokens = BORDER_PRESET_TOKENS[state.borders];
    for (const [key, val] of Object.entries(tokens)) {
      if (val) vars[key] = val;
    }
  }

  // Elevation preset — emit both --elevation-* and --shadow-* bridge vars
  if (state.elevation !== "theme") {
    const preset = ELEVATION_PRESET_TOKENS[state.elevation];
    if (preset) {
      const scheme = state.colorScheme === "dark" ? preset.dark : preset.light;
      for (const [key, val] of Object.entries(scheme)) {
        vars[key] = val;
      }
    }
  }

  // Motion preset — emit duration and easing vars.
  // The speed multiplier in state.motionSpeed overrides the preset's mult.
  const presetVars = MOTION_PRESET_VARS[state.motionPreset];
  for (const [key, val] of Object.entries(presetVars)) {
    vars[key] = val;
  }
  // Apply speed multiplier on top: recompute durations by scaling the preset's base values.
  if (state.motionPreset !== "none" && state.motionSpeed !== 1) {
    const mult = state.motionSpeed;
    const baseDurations: Record<string, number> = {
      none:    0, snappy: 100, smooth: 180, relaxed: 280, springy: 220,
    };
    const fastDurations: Record<string, number> = {
      none:    0, snappy:  60, smooth: 120, relaxed: 180, springy: 140,
    };
    const slowDurations: Record<string, number> = {
      none:    0, snappy: 160, smooth: 280, relaxed: 420, springy: 340,
    };
    const base = baseDurations[state.motionPreset] ?? 180;
    const fast = fastDurations[state.motionPreset] ?? 120;
    const slow = slowDurations[state.motionPreset] ?? 280;
    vars["--dur-base"] = `${Math.round(base * mult)}ms`;
    vars["--dur-fast"] = `${Math.round(fast * mult)}ms`;
    vars["--dur-slow"] = `${Math.round(slow * mult)}ms`;
    vars["--motion-speed-mult"] = String(mult);
  }
  // Always emit --dur-ui = calc so the bridge var works.
  vars["--dur-ui"] = `calc(${vars["--dur-base"] ?? "180ms"} * 1)`;
  vars["--dur-ui-fast"] = `calc(${vars["--dur-fast"] ?? "120ms"} * 1)`;
  vars["--dur-ui-slow"] = `calc(${vars["--dur-slow"] ?? "280ms"} * 1)`;

  return vars;
}

// ─── Preset option lists ──────────────────────────────────────────────────────

export const BORDER_PRESETS: readonly BorderPreset[] = [
  "borderless", "subtle", "default", "strong",
];

export const INPUT_LABEL_STYLES: readonly InputLabelStyle[] = [
  "stacked", "inline",
];

export const RADIUS_PRESETS = ["square", "compact", "standard", "soft", "round", "pill"] as const;
export const DENSITY_PRESETS = ["compact", "balanced", "comfortable", "spacious"] as const;
export const ELEVATION_PRESETS = ["shadowless", "flat", "subtle", "soft", "dramatic"] as const;
export const FONT_PRESETS = ["system", "inter", "humanist", "industrial", "editorial", "mono"] as const;

// ─── Labels ───────────────────────────────────────────────────────────────────

export const RADIUS_LABELS: Record<string, string> = {
  theme:    "Theme default",
  square:   "Square",
  compact:  "Compact",
  standard: "Standard",
  soft:     "Soft",
  round:    "Round",
  pill:     "Pill",
};

export const DENSITY_LABELS: Record<string, string> = {
  theme:       "Theme default",
  compact:     "Compact",
  balanced:    "Balanced",
  comfortable: "Comfortable",
  spacious:    "Spacious",
};

export const ELEVATION_LABELS: Record<string, string> = {
  theme:       "Theme default",
  shadowless:  "Shadowless",
  flat:        "Flat",
  subtle:      "Subtle",
  soft:        "Soft",
  dramatic:    "Dramatic",
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
