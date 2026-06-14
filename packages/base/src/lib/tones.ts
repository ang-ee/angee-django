// The semantic-color owner: one vocabulary for the whole React layer.
//
//   tone    = which palette  (neutral, brand, accent, info, success, …)
//   variant = how it's filled (solid, soft, surface, outline, ghost)
//
// `toneFill` is the single (tone × fill) → class matrix. Every entry is a
// LITERAL class string on purpose — Tailwind only generates utilities it can
// scan as literals in source, so `bg-${tone}-soft` interpolation would silently
// drop the class. This file is therefore the one place each fill utility is
// written out. The backing design tokens live in styles/tokens.css (+ the
// @theme bridge in styles/index.css); the tailwind-merge groups that let `cn()`
// de-dupe these classes live in lib/tailwind-merge-config.ts.

export const TONES = [
  "neutral",
  "brand",
  "accent",
  "info",
  "success",
  "warning",
  "danger",
  "purple",
  "pink",
] as const;

export type Tone = (typeof TONES)[number];

export const FILLS = ["solid", "soft", "surface", "outline", "ghost"] as const;

export type Fill = (typeof FILLS)[number];

// The solid-fill background utility per tone — the one place each `bg-*` literal
// is written. The `solid` column below and solid marks elsewhere (status dots,
// slider indicators) both read it via `toneSolidBg`, so they can't drift.
const SOLID_BG: Record<Tone, string> = {
  neutral: "bg-fg",
  brand: "bg-brand",
  accent: "bg-accent",
  info: "bg-info",
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  purple: "bg-purple",
  pink: "bg-pink",
};

/** The solid-fill background utility for a tone (just the `bg-*`, no text/border).
 *  Solid marks (dots, bars) that aren't full pills read this rather than re-listing
 *  the palette. Note: a neutral *dot* stays muted (`bg-fg-muted`) — that's a mark
 *  treatment, distinct from the solid neutral chip's `bg-fg`. */
export function toneSolidBg(tone: Tone): string {
  return SOLID_BG[tone];
}

/** (tone × fill) → Tailwind classes. The single source for both axes. */
export const toneFill: Record<Tone, Record<Fill, string>> = {
  neutral: {
    solid: `${SOLID_BG.neutral} text-fg-inverse border-fg`,
    soft: "bg-inset text-fg-2 border-border-subtle",
    surface: "bg-sheet text-fg-2 border-border",
    outline: "bg-transparent text-fg-2 border-border",
    ghost: "bg-transparent text-fg-2 border-transparent",
  },
  brand: {
    solid: `${SOLID_BG.brand} text-on-brand border-brand`,
    soft: "bg-brand-soft text-brand-soft-text border-brand-soft",
    surface: "bg-brand-tint text-brand-soft-text border-brand-line",
    outline: "bg-transparent text-brand-soft-text border-brand-line",
    ghost: "bg-transparent text-brand-soft-text border-transparent",
  },
  accent: {
    solid: `${SOLID_BG.accent} text-on-accent border-accent`,
    soft: "bg-accent-soft text-accent-soft-text border-accent-soft",
    surface: "bg-accent-tint text-accent-soft-text border-accent-line",
    outline: "bg-transparent text-accent-soft-text border-accent-line",
    ghost: "bg-transparent text-accent-soft-text border-transparent",
  },
  info: {
    solid: `${SOLID_BG.info} text-on-info border-info`,
    soft: "bg-info-soft text-info-text border-info-soft",
    surface: "bg-info-tint text-info-text border-info-line",
    outline: "bg-transparent text-info-text border-info-line",
    ghost: "bg-transparent text-info-text border-transparent",
  },
  success: {
    solid: `${SOLID_BG.success} text-on-success border-success`,
    soft: "bg-success-soft text-success-text border-success-soft",
    surface: "bg-success-tint text-success-text border-success-line",
    outline: "bg-transparent text-success-text border-success-line",
    ghost: "bg-transparent text-success-text border-transparent",
  },
  warning: {
    solid: `${SOLID_BG.warning} text-on-warning border-warning`,
    soft: "bg-warning-soft text-warning-text border-warning-soft",
    surface: "bg-warning-tint text-warning-text border-warning-line",
    outline: "bg-transparent text-warning-text border-warning-line",
    ghost: "bg-transparent text-warning-text border-transparent",
  },
  danger: {
    solid: `${SOLID_BG.danger} text-on-danger border-danger`,
    soft: "bg-danger-soft text-danger-text border-danger-soft",
    surface: "bg-danger-tint text-danger-text border-danger-line",
    outline: "bg-transparent text-danger-text border-danger-line",
    ghost: "bg-transparent text-danger-text border-transparent",
  },
  purple: {
    solid: `${SOLID_BG.purple} text-on-purple border-purple`,
    soft: "bg-purple-soft text-purple-soft-text border-purple-soft",
    surface: "bg-purple-tint text-purple-soft-text border-purple-line",
    outline: "bg-transparent text-purple-soft-text border-purple-line",
    ghost: "bg-transparent text-purple-soft-text border-transparent",
  },
  pink: {
    solid: `${SOLID_BG.pink} text-on-pink border-pink`,
    soft: "bg-pink-soft text-pink-soft-text border-pink-soft",
    surface: "bg-pink-tint text-pink-soft-text border-pink-line",
    outline: "bg-transparent text-pink-soft-text border-pink-line",
    ghost: "bg-transparent text-pink-soft-text border-transparent",
  },
};

/** The (tone × fill) class string. The one accessor every recipe routes through. */
export function toneClass(tone: Tone, fill: Fill = "soft"): string {
  return toneFill[tone][fill];
}

/** The feedback intents that carry a status glyph (a subset of the tones). */
export type FeedbackIntent = "info" | "success" | "warning" | "danger";

/**
 * The canonical icon-registry glyph name for each feedback intent. One owner for
 * "which glyph means this intent", shared by Alert, StatusIcon, and Toast so they
 * cannot drift (they previously disagreed: info was "help" vs "info").
 */
export const INTENT_GLYPHS: Record<FeedbackIntent, string> = {
  info: "info",
  success: "circle-check",
  warning: "triangle-alert",
  danger: "circle-x",
};

export type ToneValueBuckets = Partial<Record<Tone, readonly string[]>>;

export const DEFAULT_STATE_TONE_VALUES: ToneValueBuckets = {
  success: ["active", "published", "approved", "live", "open", "done"],
  warning: ["draft", "review", "pending", "in_review"],
  danger: ["error", "failed", "denied", "lost"],
  neutral: ["archived", "deleted", "rejected", "blocked"],
};

export function stateToneFromValue(
  value: string | undefined,
  buckets: ToneValueBuckets = DEFAULT_STATE_TONE_VALUES,
): Tone {
  if (!value) return "neutral";
  const normalized = value.toLowerCase();
  for (const [tone, values] of Object.entries(buckets) as [
    Tone,
    readonly string[],
  ][]) {
    if (values.includes(normalized)) return tone;
  }
  return "brand";
}
