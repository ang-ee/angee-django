import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

/**
 * Shared widget control chrome.
 *
 * Input-like widget surfaces share their visual treatment through this recipe
 * rather than subclassing a base widget. Read-only controls keep their layout
 * box but make the border transparent.
 */

export const WIDGET_CONTROL_READONLY_CLASS =
  "border-transparent bg-transparent shadow-none opacity-100 cursor-default hover:border-transparent focus:border-transparent focus-within:border-transparent focus-visible:border-transparent data-[popup-open]:border-transparent disabled:border-transparent disabled:bg-transparent disabled:opacity-100 data-[disabled]:border-transparent data-[disabled]:bg-transparent data-[disabled]:opacity-100";

export const WIDGET_CONTROL_DATA_READONLY_CLASS =
  "data-[readonly]:border-transparent data-[readonly]:bg-transparent data-[readonly]:shadow-none data-[readonly]:opacity-100 data-[readonly]:cursor-default data-[readonly]:hover:border-transparent data-[readonly]:focus:border-transparent data-[readonly]:focus-within:border-transparent data-[readonly]:focus-visible:border-transparent";

export const widgetControlSurfaceVariants = tv({
  base: "rounded-md border outline-none transition-colors",
  variants: {
    focus: {
      self: "focus:border-border-focus focus:focus-ring",
      visible: "focus-visible:border-border-focus focus-visible:focus-ring",
      within: "focus-within:border-border-focus focus-within:focus-ring",
      none: "",
    },
    invalid: {
      true: "border-danger focus:border-danger focus:focus-ring-danger focus-visible:border-danger focus-visible:focus-ring-danger focus-within:border-danger focus-within:focus-ring-danger",
      false: "",
    },
    readOnly: {
      true: WIDGET_CONTROL_READONLY_CLASS,
      false: "border-border bg-sheet",
    },
    disabled: {
      true: "cursor-not-allowed bg-inset opacity-60",
      false: "",
    },
  },
  defaultVariants: {
    focus: "self",
    invalid: false,
    readOnly: false,
    disabled: false,
  },
});

export type WidgetControlSurfaceProps = VariantProps<
  typeof widgetControlSurfaceVariants
>;

export function widgetControlSurface(
  props: WidgetControlSurfaceProps & { className?: string } = {},
): string {
  const { className, ...variants } = props;
  return cn(className, widgetControlSurfaceVariants(variants));
}
