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

/** Whether `value` is one of the known tones — the membership guard at the boundary
 *  where an untyped value (Refine meta, JSON) must be narrowed to a `Tone`. */
export function isTone(value: unknown): value is Tone {
  return TONES.includes(value as Tone);
}

/**
 * The tones a navigation menu may carry — the semantic intents (so a menu item can
 * read "danger"/"success"/… without the brand-family palettes accent/purple/pink,
 * which aren't menu vocabulary). The curated subset chrome menus narrow against;
 * `tones.ts` still owns the palette, the menu just borrows this slice of it.
 */
export const MENU_TONES = [
  "brand",
  "danger",
  "info",
  "neutral",
  "success",
  "warning",
] as const satisfies readonly Tone[];

/** A tone a navigation menu may carry — a curated subset of `Tone`. */
export type MenuTone = (typeof MENU_TONES)[number];

/** Whether `value` is one of the menu tones — the boundary guard that narrows an
 *  untyped value (Refine meta) to a `MenuTone`. */
export function isMenuTone(value: unknown): value is MenuTone {
  return (MENU_TONES as readonly unknown[]).includes(value);
}

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

// The tone-colored text utility per tone — the one place each on-surface
// `text-*` literal is written. `toneFill`'s soft/surface/outline/ghost columns
// interpolate this (mirroring how the solid column reads `SOLID_BG`), so a tone's
// text color lives once. The brand-family palettes (brand/accent/purple/pink)
// use the `-soft-text` token; the feedback palettes use plain `text-<tone>-text`;
// neutral is the body foreground, not a colored token.
const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-fg-2",
  brand: "text-brand-soft-text",
  accent: "text-accent-soft-text",
  info: "text-info-text",
  success: "text-success-text",
  warning: "text-warning-text",
  danger: "text-danger-text",
  purple: "text-purple-soft-text",
  pink: "text-pink-soft-text",
};

/** The tone-colored text utility for a tone (just the `text-*`, no bg/border) —
 *  the sibling of `toneSolidBg`/`toneGlyph`. Tone-colored text on a plain surface;
 *  for info/success/warning/danger it equals `text-<tone>-text`, and for the
 *  brand-family palettes it is the `-soft-text` token. */
export function toneText(tone: Tone): string {
  return TONE_TEXT[tone];
}

// The glyph-scoped tint per tone — the on-surface `text-*-text` colour raised to
// `!important` and targeted at a descendant `.glyph`, so a per-node tone wins over
// a container's own `[&_.glyph]:text-*` rule (which otherwise outranks a plain
// class by specificity). Lucide glyphs stroke with `currentColor`, so this
// colours the icon itself; neutral is the body foreground, not a colored token.
const GLYPH_TONE: Record<Tone, string> = {
  neutral: "[&_.glyph]:text-fg-2!",
  brand: "[&_.glyph]:text-brand-soft-text!",
  accent: "[&_.glyph]:text-accent-soft-text!",
  info: "[&_.glyph]:text-info-text!",
  success: "[&_.glyph]:text-success-text!",
  warning: "[&_.glyph]:text-warning-text!",
  danger: "[&_.glyph]:text-danger-text!",
  purple: "[&_.glyph]:text-purple-soft-text!",
  pink: "[&_.glyph]:text-pink-soft-text!",
};

/** Tint a descendant `<Glyph>` with a tone, overriding a container's glyph color.
 *  Use on the wrapper of an icon that must show its own tone inside a row/list
 *  whose recipe already sets `[&_.glyph]:text-*` (e.g. a starred tree row). */
export function toneGlyph(tone: Tone): string {
  return GLYPH_TONE[tone];
}

/** (tone × fill) → Tailwind classes. The single source for both axes. */
export const toneFill: Record<Tone, Record<Fill, string>> = {
  neutral: {
    solid: `${SOLID_BG.neutral} text-fg-inverse border-fg`,
    soft: `bg-inset ${TONE_TEXT.neutral} border-border-subtle`,
    surface: `bg-sheet ${TONE_TEXT.neutral} border-border`,
    outline: `bg-transparent ${TONE_TEXT.neutral} border-border`,
    ghost: `bg-transparent ${TONE_TEXT.neutral} border-transparent`,
  },
  brand: {
    solid: `${SOLID_BG.brand} text-on-brand border-brand`,
    soft: `bg-brand-soft ${TONE_TEXT.brand} border-brand-soft`,
    surface: `bg-brand-tint ${TONE_TEXT.brand} border-brand-line`,
    outline: `bg-transparent ${TONE_TEXT.brand} border-brand-line`,
    ghost: `bg-transparent ${TONE_TEXT.brand} border-transparent`,
  },
  accent: {
    solid: `${SOLID_BG.accent} text-on-accent border-accent`,
    soft: `bg-accent-soft ${TONE_TEXT.accent} border-accent-soft`,
    surface: `bg-accent-tint ${TONE_TEXT.accent} border-accent-line`,
    outline: `bg-transparent ${TONE_TEXT.accent} border-accent-line`,
    ghost: `bg-transparent ${TONE_TEXT.accent} border-transparent`,
  },
  info: {
    solid: `${SOLID_BG.info} text-on-info border-info`,
    soft: `bg-info-soft ${TONE_TEXT.info} border-info-soft`,
    surface: `bg-info-tint ${TONE_TEXT.info} border-info-line`,
    outline: `bg-transparent ${TONE_TEXT.info} border-info-line`,
    ghost: `bg-transparent ${TONE_TEXT.info} border-transparent`,
  },
  success: {
    solid: `${SOLID_BG.success} text-on-success border-success`,
    soft: `bg-success-soft ${TONE_TEXT.success} border-success-soft`,
    surface: `bg-success-tint ${TONE_TEXT.success} border-success-line`,
    outline: `bg-transparent ${TONE_TEXT.success} border-success-line`,
    ghost: `bg-transparent ${TONE_TEXT.success} border-transparent`,
  },
  warning: {
    solid: `${SOLID_BG.warning} text-on-warning border-warning`,
    soft: `bg-warning-soft ${TONE_TEXT.warning} border-warning-soft`,
    surface: `bg-warning-tint ${TONE_TEXT.warning} border-warning-line`,
    outline: `bg-transparent ${TONE_TEXT.warning} border-warning-line`,
    ghost: `bg-transparent ${TONE_TEXT.warning} border-transparent`,
  },
  danger: {
    solid: `${SOLID_BG.danger} text-on-danger border-danger`,
    soft: `bg-danger-soft ${TONE_TEXT.danger} border-danger-soft`,
    surface: `bg-danger-tint ${TONE_TEXT.danger} border-danger-line`,
    outline: `bg-transparent ${TONE_TEXT.danger} border-danger-line`,
    ghost: `bg-transparent ${TONE_TEXT.danger} border-transparent`,
  },
  purple: {
    solid: `${SOLID_BG.purple} text-on-purple border-purple`,
    soft: `bg-purple-soft ${TONE_TEXT.purple} border-purple-soft`,
    surface: `bg-purple-tint ${TONE_TEXT.purple} border-purple-line`,
    outline: `bg-transparent ${TONE_TEXT.purple} border-purple-line`,
    ghost: `bg-transparent ${TONE_TEXT.purple} border-transparent`,
  },
  pink: {
    solid: `${SOLID_BG.pink} text-on-pink border-pink`,
    soft: `bg-pink-soft ${TONE_TEXT.pink} border-pink-soft`,
    surface: `bg-pink-tint ${TONE_TEXT.pink} border-pink-line`,
    outline: `bg-transparent ${TONE_TEXT.pink} border-pink-line`,
    ghost: `bg-transparent ${TONE_TEXT.pink} border-transparent`,
  },
};

/** The (tone × fill) class string. The one accessor every recipe routes through. */
export function toneClass(tone: Tone, fill: Fill = "soft"): string {
  return toneFill[tone][fill];
}

/** The feedback intents that carry a status glyph (a curated subset of the tones). */
export const FEEDBACK_INTENTS = [
  "info",
  "success",
  "warning",
  "danger",
] as const satisfies readonly Tone[];

/** A feedback intent — a tone that carries a status glyph. */
export type FeedbackIntent = (typeof FEEDBACK_INTENTS)[number];

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

/**
 * A `tone → values` map: which status-string values render in which tone. This is
 * the pure data shape; the vocabulary itself is a caller fact (the status names a
 * product uses), not a framework color fact — `tones.ts` owns the color mechanism,
 * never the domain values. See the shared `STATUS_TONES` (widgets/status-tones.ts)
 * for the status convention, or pass an explicit `<Column tone>` map.
 */
export type ToneValueBuckets = Partial<Record<Tone, readonly string[]>>;

/**
 * Resolve a status-string `value` to a tone via a caller-supplied `buckets` map:
 * the bucket whose list contains `value.toLowerCase()` (so bucket entries must be
 * lowercase), else `brand`, and `neutral` for an empty value. A pure mechanism —
 * `buckets` carries the product vocabulary, so the framework holds no domain
 * status names.
 */
export function stateToneFromValue(
  value: string | undefined,
  buckets: ToneValueBuckets,
): Tone {
  if (!value) return "neutral";
  const normalized = value.toLowerCase();
  for (const tone of TONES) {
    if (buckets[tone]?.includes(normalized)) return tone;
  }
  return "brand";
}
