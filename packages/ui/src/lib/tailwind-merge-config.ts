import type { extendTailwindMerge } from "tailwind-merge";

import { TONES } from "./tones";

type TailwindMergeConfig = Parameters<typeof extendTailwindMerge>[0];

const FONT_SIZE_TOKENS = ["2xs", "13", "15", "22", "28", "34"];

// The two-axis fill matrix (lib/tones.ts) emits, per vivid palette: `bg-P`
// (solid) / `bg-P-tint` (surface), `border-P` (solid) / `border-P-soft` /
// `border-P-line`, and `text-on-P` (solid). Derive the merge-group members from
// the same TONES constant so this inventory can't drift from the matrix. (This
// file is plain runtime config — NOT Tailwind-scanned — so interpolation is
// safe here, unlike the literal matrix in lib/tones.ts.)
const VIVID_TONES = TONES.filter((tone) => tone !== "neutral");

const FG_COLOR_TOKENS = [
  "fg",
  "fg-2",
  "fg-muted",
  "fg-subtle",
  "fg-inverse",
  "on-rail",
  "on-rail-mut",
  "on-rail-hi",
  "on-brand",
  "on-danger",
  "link",
  "brand-soft-text",
  "accent-soft-text",
  "success-text",
  "warning-text",
  "danger-text",
  "info-text",
  "purple-soft-text",
  "pink-soft-text",
  ...VIVID_TONES.map((tone) => `on-${tone}`),
];

const BG_COLOR_TOKENS = [
  "canvas",
  "sheet",
  "sheet-2",
  "rail",
  "rail-hi",
  "rail-hover",
  "avatar-default-bg",
  "kbd-on-rail",
  "popover",
  "inset",
  "overlay",
  "brand",
  "brand-hover",
  "brand-active",
  "brand-soft",
  "danger",
  "danger-hover",
  "danger-active",
  "accent-soft",
  "success-soft",
  "warning-soft",
  "danger-soft",
  "info-soft",
  "purple-soft",
  "pink-soft",
  ...VIVID_TONES,
  ...VIVID_TONES.map((tone) => `${tone}-tint`),
];

const BORDER_COLOR_TOKENS = [
  "border",
  "border-subtle",
  "border-strong",
  "border-focus",
  "border-on-rail",
  "brand",
  "brand-hover",
  "brand-active",
  "danger",
  "danger-hover",
  "danger-active",
  "danger-soft",
  "kbd-on-rail-border",
  "info-soft",
  "success-soft",
  "warning-soft",
  ...VIVID_TONES,
  ...VIVID_TONES.map((tone) => `${tone}-soft`),
  ...VIVID_TONES.map((tone) => `${tone}-line`),
];

const RADIUS_TOKENS = ["2", "4", "6", "8", "10", "12"];

/**
 * One merge config for every class-composition path in @angee/base.
 *
 * Tailwind Merge does not know Angee's @theme names. Without these groups,
 * `text-13 text-on-brand` is treated as two text-color utilities, so either
 * the font size or foreground color disappears depending on class order.
 */
export const ANGEE_TW_MERGE_CONFIG = {
  extend: {
    classGroups: {
      "font-size": [{ text: FONT_SIZE_TOKENS }],
      "text-color": [{ text: FG_COLOR_TOKENS }],
      "bg-color": [{ bg: BG_COLOR_TOKENS }],
      "border-color": [{ border: BORDER_COLOR_TOKENS }],
      rounded: [{ rounded: RADIUS_TOKENS }],
    },
  },
} satisfies TailwindMergeConfig;
