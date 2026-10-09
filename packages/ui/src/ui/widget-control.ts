import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

/**
 * Shared widget control chrome.
 *
 * Two layers own the cross-cutting interactive treatment so controls extend an
 * owner instead of re-declaring the same fragments:
 *
 * - {@link interactiveSurfaceVariants} (L0) owns "focusable / disablable
 *   element": the focus ring and the disabled dimming. Buttons, toggles,
 *   collapsibles and toolbars reuse it directly.
 * - {@link widgetControlSurfaceVariants} (L1) extends L0 and owns the input box
 *   chrome: the rounded-6 border, surface fill, focus border highlight, the
 *   invalid danger ring (focus-aware, via compound variants) and the read-only
 *   transparent treatment. Input-like controls extend or compose it.
 *
 * Read-only controls keep their layout box but make the border transparent.
 */

export const WIDGET_CONTROL_READONLY_CLASS =
  "border-transparent bg-transparent shadow-none opacity-100 cursor-default hover:border-transparent focus:border-transparent focus-within:border-transparent focus-visible:border-transparent data-[popup-open]:border-transparent disabled:border-transparent disabled:bg-transparent disabled:opacity-100 data-[disabled]:border-transparent data-[disabled]:bg-transparent data-[disabled]:opacity-100";

export const WIDGET_CONTROL_DATA_READONLY_CLASS =
  "data-[readonly]:border-transparent data-[readonly]:bg-transparent data-[readonly]:shadow-none data-[readonly]:opacity-100 data-[readonly]:cursor-default data-[readonly]:hover:border-transparent data-[readonly]:focus:border-transparent data-[readonly]:focus-within:border-transparent data-[readonly]:focus-visible:border-transparent";

/**
 * L0 — the focusable / disablable interactive element.
 *
 * `focus` selects which pseudo carries the focus ring; `disabled` selects the
 * dimming mechanism (native `:disabled` pseudo vs base-ui `data-[disabled]`).
 */
export const interactiveSurfaceVariants = tv({
  // cursor-pointer is the correct default for every interactive element.
  // Disabled variants override it with cursor-not-allowed via compound specificity.
  base: "cursor-pointer outline-none transition-colors",
  variants: {
    focus: {
      self: "focus:focus-ring",
      visible: "focus-visible:focus-ring",
      within: "focus-within:focus-ring",
      none: "",
    },
    disabled: {
      pseudo: "disabled:cursor-not-allowed disabled:opacity-60",
      data: "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60",
      none: "",
    },
  },
  defaultVariants: {
    focus: "none",
    disabled: "none",
  },
});

export type InteractiveSurfaceProps = VariantProps<
  typeof interactiveSurfaceVariants
>;

export function interactiveSurface(
  props: InteractiveSurfaceProps & { className?: string } = {},
): string {
  const { className, ...variants } = props;
  return cn(className, interactiveSurfaceVariants(variants));
}

/**
 * L1 — the input box chrome. Extends L0 so the focus ring and disabled dimming
 * flow from the owner; this layer adds the border, surface fill, focus border
 * highlight, the focus-aware invalid danger ring and the read-only treatment.
 *
 * `disabled` is declared before `readOnly` so a read-only + disabled control
 * keeps the read-only neutralizers (`disabled:opacity-100` / `bg-transparent`).
 * The danger ring lives in compound variants (applied last) so its resting
 * `border-danger` wins over the surface border, and every danger compound is
 * guarded on `readOnly: false` so a read-only invalid control stays transparent.
 * `presentation: "cell"` reveals plain controls and their marked affordances on
 * row hover or focus; callers compose its surface and size through the helper below.
 */
export const widgetControlSurfaceVariants = tv({
  extend: interactiveSurfaceVariants,
  base: "rounded-6 border",
  variants: {
    surface: {
      sheet: "border-border bg-sheet",
      inset: "border-border bg-inset hover:border-border-strong",
      plain: "border-transparent bg-transparent",
      none: "",
    },
    presentation: {
      form: "",
      cell: "border-transparent bg-transparent shadow-none [&_[data-widget-affordance]]:opacity-0 group-hover/record:[&_[data-widget-affordance]]:opacity-100 group-focus-within/record:[&_[data-widget-affordance]]:opacity-100 focus-within:[&_[data-widget-affordance]]:opacity-100 data-[popup-open]:[&_[data-widget-affordance]]:opacity-100",
    },
    focus: {
      self: "focus:border-border-focus",
      visible: "focus-visible:border-border-focus",
      within: "focus-within:border-border-focus",
      none: "",
    },
    invalid: {
      true: "",
      false: "",
    },
    disabled: {
      pseudo: "disabled:bg-inset",
      data: "data-[disabled]:bg-inset",
      none: "",
    },
    readOnly: {
      true: WIDGET_CONTROL_READONLY_CLASS,
      false: "",
    },
  },
  compoundVariants: [
    {
      presentation: "cell",
      readOnly: false,
      class: "group-hover/record:border-border group-focus-within/record:border-border hover:border-border focus:border-border-focus focus-visible:border-border-focus focus-within:border-border-focus data-[popup-open]:border-border-focus",
    },
    { invalid: true, readOnly: false, class: "border-danger" },
    {
      presentation: "cell",
      invalid: true,
      readOnly: false,
      class: "group-hover/record:border-danger group-focus-within/record:border-danger hover:border-danger focus:border-danger focus-visible:border-danger focus-within:border-danger data-[popup-open]:border-danger",
    },
    {
      invalid: true,
      readOnly: false,
      focus: "self",
      class: "focus:border-danger focus:focus-ring-danger",
    },
    {
      invalid: true,
      readOnly: false,
      focus: "within",
      class: "focus-within:border-danger focus-within:focus-ring-danger",
    },
    {
      invalid: true,
      readOnly: false,
      focus: "visible",
      class: "focus-visible:border-danger focus-visible:focus-ring-danger",
    },
  ],
  defaultVariants: {
    surface: "sheet",
    presentation: "form",
    focus: "self",
    invalid: false,
    readOnly: false,
    disabled: "pseudo",
  },
});

export type WidgetControlSurfaceProps = VariantProps<
  typeof widgetControlSurfaceVariants
>;

/** Cell controls compose the existing compact size and plain surface in every recipe. */
export function widgetControlPresentationProps(
  presentation: WidgetControlSurfaceProps["presentation"],
) {
  return presentation === "cell"
    ? { presentation, size: "sm" as const, surface: "plain" as const }
    : { presentation };
}

export function widgetControlSurface(
  props: WidgetControlSurfaceProps & { className?: string } = {},
): string {
  const { className, ...variants } = props;
  return cn(className, widgetControlSurfaceVariants(variants));
}
