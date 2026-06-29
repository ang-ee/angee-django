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

// The named z-index ladder from styles/index.css (`--z-*`). Registering them as
// one group lets `cn()`/`tv()` dedupe named stops — e.g. the drawer overlay
// passing `z-drawer` overrides the drawer recipe's default `z-modal` instead of
// leaving both classes to fight on stylesheet order.
const Z_INDEX_TOKENS = [
  "sticky-cell",
  "sticky-col",
  "bulk-bar",
  "topbar",
  "rail",
  "breadcrumb",
  "drawer",
  "popover",
  "toast",
  "tooltip",
  "modal-backdrop",
  "modal",
];

/**
 * One merge config for every class-composition path in @angee/ui.
 *
 * Tailwind Merge does not know Angee's @theme names. Without these groups,
 * `text-13 text-on-brand` is treated as two text-color utilities, so either
 * the font size or foreground color disappears depending on class order.
 *
 * Radius is fed through the `radius` THEME, not a single `rounded` classGroup:
 * Tailwind Merge derives every rounded utility (`rounded`, the side groups
 * `rounded-t/-b/-l/-r`, and the corner groups `rounded-tl/-tr/-bl/-br`) from
 * `theme.radius` via `fromTheme("radius")`. Extending the theme registers the
 * canonical `rounded-N` scale across all of them at once, so corner/side
 * spellings like `rounded-t-10` de-dupe under `cn()` — a per-classGroup
 * `rounded` extension would only cover the all-corners group and leave the
 * side/corner subgroups blind to the tokens. Default keys (`none`/`full`/the
 * Tailwind named scale) are preserved because this is `extend`, not `override`.
 */
export const ANGEE_TW_MERGE_CONFIG = {
  extend: {
    theme: {
      radius: RADIUS_TOKENS,
    },
    classGroups: {
      "font-size": [{ text: FONT_SIZE_TOKENS }],
      "text-color": [{ text: FG_COLOR_TOKENS }],
      "bg-color": [{ bg: BG_COLOR_TOKENS }],
      "border-color": [{ border: BORDER_COLOR_TOKENS }],
      "z-index": [{ z: Z_INDEX_TOKENS }],
    },
  },
} satisfies TailwindMergeConfig;
