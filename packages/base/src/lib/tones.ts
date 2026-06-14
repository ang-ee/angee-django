export type ToneName =
  | "default"
  | "brand"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "purple"
  | "pink";

export type ToneSlots = {
  bg: string;
  fg: string;
  border: string;
  badge: string;
  dotFg: string;
  barFg: string;
};

export const tones: Record<ToneName, ToneSlots> = {
  default: {
    bg: "bg-inset",
    fg: "text-fg-2",
    border: "border-border-subtle",
    badge: "bg-inset text-fg-2",
    dotFg: "text-fg-muted",
    barFg: "bg-fg-muted",
  },
  brand: {
    bg: "bg-brand-soft",
    fg: "text-brand-soft-text",
    border: "border-brand-soft",
    badge: "bg-brand-soft text-brand-soft-text",
    dotFg: "text-brand-soft-text",
    barFg: "bg-brand-soft-text",
  },
  accent: {
    bg: "bg-accent-soft",
    fg: "text-accent-soft-text",
    border: "border-accent-soft",
    badge: "bg-accent-soft text-accent-soft-text",
    dotFg: "text-accent-soft-text",
    barFg: "bg-accent-soft-text",
  },
  success: {
    bg: "bg-success-soft",
    fg: "text-success-text",
    border: "border-success-soft",
    badge: "bg-success-soft text-success-text",
    dotFg: "text-success-text",
    barFg: "bg-success-text",
  },
  warning: {
    bg: "bg-warning-soft",
    fg: "text-warning-text",
    border: "border-warning-soft",
    badge: "bg-warning-soft text-warning-text",
    dotFg: "text-warning-text",
    barFg: "bg-warning-text",
  },
  danger: {
    bg: "bg-danger-soft",
    fg: "text-danger-text",
    border: "border-danger-soft",
    badge: "bg-danger-soft text-danger-text",
    dotFg: "text-danger-text",
    barFg: "bg-danger-text",
  },
  info: {
    bg: "bg-info-soft",
    fg: "text-info-text",
    border: "border-info-soft",
    badge: "bg-info-soft text-info-text",
    dotFg: "text-info-text",
    barFg: "bg-info-text",
  },
  purple: {
    bg: "bg-purple-soft",
    fg: "text-purple-soft-text",
    border: "border-purple-soft",
    badge: "bg-purple-soft text-purple-soft-text",
    dotFg: "text-purple-soft-text",
    barFg: "bg-purple-soft-text",
  },
  pink: {
    bg: "bg-pink-soft",
    fg: "text-pink-soft-text",
    border: "border-pink-soft",
    badge: "bg-pink-soft text-pink-soft-text",
    dotFg: "text-pink-soft-text",
    barFg: "bg-pink-soft-text",
  },
};

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

export type ToneValueBuckets = Partial<Record<ToneName, readonly string[]>>;

export const DEFAULT_STATE_TONE_VALUES: ToneValueBuckets = {
  success: ["active", "published", "approved", "live", "open", "done"],
  warning: ["draft", "review", "pending", "in_review"],
  danger: ["error", "failed", "denied", "lost"],
  default: ["archived", "deleted", "rejected", "blocked"],
};

export function stateToneFromValue(
  value: string | undefined,
  buckets: ToneValueBuckets = DEFAULT_STATE_TONE_VALUES,
): ToneName {
  if (!value) return "default";
  const normalized = value.toLowerCase();
  for (const [tone, values] of Object.entries(buckets) as [
    ToneName,
    readonly string[],
  ][]) {
    if (values.includes(normalized)) return tone;
  }
  return "brand";
}
