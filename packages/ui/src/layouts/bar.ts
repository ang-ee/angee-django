import { tv, type VariantProps } from "../lib/variants";
import { textRoleVariants } from "../ui/text";

// The single owner of "bar chrome" — the flush horizontal frame shared by every
// fixed-height (or padding-sized) bar in the shell and on a page. It decomposes
// the chrome that the eight bars hand-spell today into orthogonal axes so a bar
// declares intent (`height`/`edge`/`tone`/`pad`/`justify`/…) instead of
// re-typing `flex … border-b border-border-subtle bg-sheet px-4` literals.
//
// Axes
// - `height`  the fixed row height token (or `none`, for padding-sized bars).
// - `edge`    which side carries the divider rule (`top`/`bottom`/`none`); the
//             COLOR comes from `tone`, so a border-color util with no width is a
//             no-op and `edge:none` renders no rule.
// - `tone`    the surface: background + matching border color (+ on-rail text).
// - `pad`     the horizontal/vertical padding rhythm; `flush` is px-only for
//             fixed-height bars, the rest pair px with py for padding-sized bars.
// - `gap`/`align`/`justify`/`text`  the remaining flex + typography knobs the
//             bars vary (gap-1…4, items-start for header-style bars, the muted
//             secondary text used by breadcrumb/footer/statusline).
//
// Owner relationship to the existing recipes (Bars stage wires the consumers):
// - SUBSUMES the hand-spelled shell strings of the non-recipe bars — `TopBar`,
//   `Breadcrumb`, `ControlBand`, `Statusline`, chat `ChatBar`/`ChatHeader`:
//   their `cn("flex … bg-sheet px-4", …)` literal becomes `barVariants({…})`.
// - SUBSUMES `pageFooterVariants` (a pure shell recipe — only its `sticky`
//   knob stays bar-specific).
// - COMPOSES INTO `pageToolbarVariants` / `pageHeaderVariants`: barVariants owns
//   their `root` shell while each keeps its extra slots (start/end, main/crumbs/
//   eyebrow/title/description/actions) and its `sticky`/`density` knobs.
// - COMPOSES INTO `toolbarVariants` `surface:chrome` (which duplicates the
//   control-band shell) — barVariants feeds that root; toolbarVariants stays the
//   owner of its button/input/separator slots.
// - `createLayoutBand` is untouched: it keeps owning the portal logic for the
//   two layout bands; only the base class strings it is handed move here.
//
// Pixel note for the Bars stage: the bars carry drifted horizontal padding
// (px-3 / px-3.5 / px-4 / px-5) and gaps that this scale normalizes. A bar that
// must keep a non-scale value (TopBar's asymmetric `pl-4`, Statusline's `px-3.5`)
// passes it via `className`; intentional convergence is recorded when wired.
export const barVariants = tv({
  base: "flex min-w-0 shrink-0",
  variants: {
    height: {
      none: "",
      topbar: "h-topbar-h",
      crumbs: "h-crumbs-h",
      control: "h-control-h",
      controlMin: "min-h-control-h",
      status: "h-7",
    },
    edge: {
      none: "",
      top: "border-t",
      bottom: "border-b",
    },
    tone: {
      none: "",
      sheet: "border-border-subtle bg-sheet",
      sheet2: "border-border-subtle bg-sheet-2",
      rail: "border-border-on-rail bg-rail text-on-rail",
    },
    pad: {
      none: "",
      flush: "px-4",
      compact: "px-3 py-1.5",
      comfortable: "px-4 py-2",
      compactTall: "px-4 py-2.5",
      comfortableTall: "px-5 py-3",
    },
    gap: {
      1: "gap-1",
      2: "gap-2",
      3: "gap-3",
      4: "gap-4",
    },
    align: {
      center: "items-center",
      start: "items-start",
    },
    justify: {
      start: "",
      between: "justify-between",
      end: "justify-end",
    },
    // Routed through the textRoleVariants recipe so the muted secondary literals
    // live in exactly one place (`13-muted`=meta, `2xs-muted`=caption).
    text: {
      none: "",
      "13-muted": textRoleVariants({ role: "meta" }),
      "2xs-muted": textRoleVariants({ role: "caption" }),
    },
  },
  defaultVariants: {
    height: "none",
    edge: "none",
    tone: "sheet",
    pad: "none",
    align: "center",
    justify: "start",
    text: "none",
  },
});

export type BarRecipeProps = VariantProps<typeof barVariants>;
