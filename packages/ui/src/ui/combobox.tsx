import * as React from "react";
import { Combobox as BaseCombobox } from "@base-ui/react/combobox";
import type {
  ComboboxChipsProps as BaseComboboxChipsProps,
  ComboboxEmptyProps as BaseComboboxEmptyProps,
  ComboboxGroupLabelProps as BaseComboboxGroupLabelProps,
  ComboboxIconProps as BaseComboboxIconProps,
  ComboboxInputProps as BaseComboboxInputProps,
  ComboboxItemIndicatorProps as BaseComboboxItemIndicatorProps,
  ComboboxItemProps as BaseComboboxItemProps,
  ComboboxListProps as BaseComboboxListProps,
  ComboboxPopupProps as BaseComboboxPopupProps,
  ComboboxPositionerProps as BaseComboboxPositionerProps,
  ComboboxTriggerProps as BaseComboboxTriggerProps,
} from "@base-ui/react/combobox";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { PORTALED_CONTROL_LAYER } from "./popover";
import { selectVariants, type SelectSize } from "./select";
import { textRoleVariants } from "./text";
import { widgetControlPresentationProps, widgetControlSurface, type WidgetControlSurfaceProps } from "./widget-control";

/**
 * The one option-list-with-a-query primitive, over Base UI's `Combobox`: a
 * trigger or an inline input (or a chips row holding picked values), one popup
 * positioned on the portaled control layer, and the option list sharing the
 * `Select` recipe so every picker looks the same. Base UI owns item identity,
 * selection, chip focus and keyboard navigation; `Command` stays the palette's
 * engine only.
 */

/**
 * @kind token
 */
export const comboboxVariants = {
  /** The bare query input: the row or popup head around it owns the box. */
  input: "min-w-0 flex-1 border-0 bg-transparent text-13 text-fg outline-none placeholder:text-fg-muted",
  /** The popup head holding a query input above the list. */
  search: "flex h-8 items-center gap-2 border-b border-border-subtle px-2 text-fg-muted",
  empty: cn(textRoleVariants({ role: "meta" }), "px-2 py-3 empty:hidden"),
} as const;

/**
 * @owner Combobox
 */
export const ComboboxRoot = BaseCombobox.Root;
export type ComboboxValueProps = React.ComponentProps<typeof BaseCombobox.Value> & { className?: string };

/** The picked value (or the placeholder) inside the field-shaped trigger.
 * @owner Combobox
 */
export function ComboboxValue({ className, ...props }: ComboboxValueProps): React.ReactElement {
  const styles = selectVariants();
  return <span className={styles.value({ className })}><BaseCombobox.Value {...props} /></span>;
}
/**
 * @owner Combobox
 */
export const ComboboxChip = BaseCombobox.Chip;
/**
 * @owner Combobox
 */
export const ComboboxGroup = BaseCombobox.Group;
/**
 * @owner Combobox
 */
export const ComboboxCollection = BaseCombobox.Collection;

export type ComboboxTriggerProps = Omit<BaseComboboxTriggerProps, "className"> &
  Pick<WidgetControlSurfaceProps, "presentation" | "surface" | "invalid" | "readOnly"> & {
    className?: string;
    size?: SelectSize;
    /** `field`: the select-shaped box holding the value and caret; `caret`: the caret alone, inside a chips row. */
    shape?: "field" | "caret";
  };

/** The trigger: the select-shaped field (value, then caret) or a bare caret in a chips row.
 * @owner Combobox
 */
export const ComboboxTrigger = React.forwardRef<HTMLButtonElement, ComboboxTriggerProps>(
  function ComboboxTrigger(
    { children, className, invalid, presentation, readOnly, shape = "field", size, surface = "inset", ...props },
    ref,
  ) {
    const cell = widgetControlPresentationProps(presentation);
    const styles = selectVariants({ size: cell.size ?? size ?? "md" });
    if (shape === "caret") {
      return (
        <BaseCombobox.Trigger
          ref={ref}
          data-widget-affordance=""
          className={styles.icon({ className: cn("ml-auto shrink-0 cursor-pointer", className) })}
          {...props}
        >
          {children ?? <Glyph name="chevron-down" />}
        </BaseCombobox.Trigger>
      );
    }
    return (
      <BaseCombobox.Trigger
        ref={ref}
        className={styles.trigger({
          className: cn(
            widgetControlSurface({ focus: "visible", surface, ...cell, invalid, readOnly, disabled: "data" }),
            className,
          ),
        })}
        {...props}
      >
        {children}
      </BaseCombobox.Trigger>
    );
  },
);
ComboboxTrigger.displayName = "ComboboxTrigger";

export type ComboboxIconProps = Omit<BaseComboboxIconProps, "className"> & { className?: string };

/** The caret; hidden in a cell until the record is hovered or focused.
 * @owner Combobox
 */
export const ComboboxIcon = React.forwardRef<HTMLSpanElement, ComboboxIconProps>(
  function ComboboxIcon({ children, className, ...props }, ref) {
    const styles = selectVariants();
    return (
      <BaseCombobox.Icon ref={ref} data-widget-affordance="" className={styles.icon({ className })} {...props}>
        {children ?? <Glyph name="chevron-down" />}
      </BaseCombobox.Icon>
    );
  },
);
ComboboxIcon.displayName = "ComboboxIcon";

export type ComboboxInputProps = Omit<BaseComboboxInputProps, "className"> & { className?: string };

/** The query input, bare: a chips row, a search box or the popup head frames it.
 * @owner Combobox
 */
export const ComboboxInput = React.forwardRef<HTMLInputElement, ComboboxInputProps>(
  function ComboboxInput({ className, ...props }, ref) {
    return <BaseCombobox.Input ref={ref} className={cn(comboboxVariants.input, className)} {...props} />;
  },
);
ComboboxInput.displayName = "ComboboxInput";

export type ComboboxSearchProps = ComboboxInputProps;

/** The popup head: a search glyph and the query input above the list.
 * @owner Combobox
 */
export const ComboboxSearch = React.forwardRef<HTMLInputElement, ComboboxSearchProps>(
  function ComboboxSearch({ className, placeholder, ...props }, ref) {
    const t = useUiT();
    return (
      <label className={cn(comboboxVariants.search, className)}>
        <Glyph name="search" className="shrink-0" />
        <ComboboxInput
          ref={ref}
          aria-label={t("combobox.searchOptions")}
          placeholder={placeholder ?? t("combobox.search")}
          {...props}
        />
      </label>
    );
  },
);
ComboboxSearch.displayName = "ComboboxSearch";

export type ComboboxChipsProps = Omit<BaseComboboxChipsProps, "className"> & { className?: string };

/** The row of picked values and the inline query input; the caller gives it its box.
 * @owner Combobox
 */
export const ComboboxChips = React.forwardRef<HTMLDivElement, ComboboxChipsProps>(
  function ComboboxChips({ className, ...props }, ref) {
    return <BaseCombobox.Chips ref={ref} className={cn("flex min-w-0 items-center gap-1", className)} {...props} />;
  },
);
ComboboxChips.displayName = "ComboboxChips";

export type ComboboxPopupProps = Omit<BaseComboboxPopupProps, "className"> &
  Pick<BaseComboboxPositionerProps, "anchor" | "align" | "side" | "sideOffset" | "alignOffset"> & {
    className?: string;
    size?: SelectSize;
  };

/** The portaled popup on the control layer, anchored to the trigger or a given box.
 * @owner Combobox
 */
export const ComboboxPopup = React.forwardRef<HTMLDivElement, ComboboxPopupProps>(
  function ComboboxPopup({ align, alignOffset, anchor, className, side, sideOffset = 4, size = "md", ...props }, ref) {
    const styles = selectVariants({ size });
    return (
      <BaseCombobox.Portal>
        <BaseCombobox.Positioner
          className={PORTALED_CONTROL_LAYER}
          anchor={anchor}
          align={align}
          alignOffset={alignOffset}
          side={side}
          sideOffset={sideOffset}
        >
          <BaseCombobox.Popup ref={ref} className={styles.content({ className })} {...props} />
        </BaseCombobox.Positioner>
      </BaseCombobox.Portal>
    );
  },
);
ComboboxPopup.displayName = "ComboboxPopup";

export type ComboboxListProps = Omit<BaseComboboxListProps, "className"> & { className?: string };

/**
 * @owner Combobox
 */
export const ComboboxList = React.forwardRef<HTMLDivElement, ComboboxListProps>(
  function ComboboxList({ className, ...props }, ref) {
    const styles = selectVariants();
    return <BaseCombobox.List ref={ref} className={styles.list({ className })} {...props} />;
  },
);
ComboboxList.displayName = "ComboboxList";

export type ComboboxItemProps = Omit<BaseComboboxItemProps, "className"> & {
  className?: string;
  size?: SelectSize;
};

/**
 * @owner Combobox
 */
export const ComboboxItem = React.forwardRef<HTMLDivElement, ComboboxItemProps>(
  function ComboboxItem({ className, size = "md", ...props }, ref) {
    const styles = selectVariants({ size });
    return <BaseCombobox.Item ref={ref} className={styles.item({ className })} {...props} />;
  },
);
ComboboxItem.displayName = "ComboboxItem";

export type ComboboxItemTextProps = React.HTMLAttributes<HTMLSpanElement> & { className?: string };

/**
 * @owner Combobox
 */
export const ComboboxItemText = React.forwardRef<HTMLSpanElement, ComboboxItemTextProps>(
  function ComboboxItemText({ className, ...props }, ref) {
    const styles = selectVariants();
    return <span ref={ref} className={styles.itemText({ className })} {...props} />;
  },
);
ComboboxItemText.displayName = "ComboboxItemText";

export type ComboboxItemIndicatorProps = Omit<BaseComboboxItemIndicatorProps, "className"> & { className?: string };

/**
 * @owner Combobox
 */
export const ComboboxItemIndicator = React.forwardRef<HTMLSpanElement, ComboboxItemIndicatorProps>(
  function ComboboxItemIndicator({ children, className, ...props }, ref) {
    const styles = selectVariants();
    return (
      <BaseCombobox.ItemIndicator ref={ref} className={styles.indicator({ className })} {...props}>
        {children ?? <Glyph name="check" />}
      </BaseCombobox.ItemIndicator>
    );
  },
);
ComboboxItemIndicator.displayName = "ComboboxItemIndicator";

export type ComboboxGroupLabelProps = Omit<BaseComboboxGroupLabelProps, "className"> & { className?: string };

/**
 * @owner Combobox
 */
export const ComboboxGroupLabel = React.forwardRef<HTMLDivElement, ComboboxGroupLabelProps>(
  function ComboboxGroupLabel({ className, ...props }, ref) {
    const styles = selectVariants();
    return <BaseCombobox.GroupLabel ref={ref} className={styles.label({ className })} {...props} />;
  },
);
ComboboxGroupLabel.displayName = "ComboboxGroupLabel";

export type ComboboxEmptyProps = Omit<BaseComboboxEmptyProps, "className"> & { className?: string };

/** "No options" when the query matches nothing; hidden while it has no text.
 * @owner Combobox
 */
export const ComboboxEmpty = React.forwardRef<HTMLDivElement, ComboboxEmptyProps>(
  function ComboboxEmpty({ children, className, ...props }, ref) {
    const t = useUiT();
    return (
      <BaseCombobox.Empty ref={ref} className={cn(comboboxVariants.empty, className)} {...props}>
        {children ?? t("combobox.noOptions")}
      </BaseCombobox.Empty>
    );
  },
);
ComboboxEmpty.displayName = "ComboboxEmpty";

/**
 * @kind primitive
 */
export const Combobox = Object.assign(ComboboxRoot, {
  Root: ComboboxRoot,
  Trigger: ComboboxTrigger,
  Value: ComboboxValue,
  Icon: ComboboxIcon,
  Input: ComboboxInput,
  Search: ComboboxSearch,
  Chips: ComboboxChips,
  Chip: ComboboxChip,
  Popup: ComboboxPopup,
  List: ComboboxList,
  Item: ComboboxItem,
  ItemText: ComboboxItemText,
  ItemIndicator: ComboboxItemIndicator,
  Group: ComboboxGroup,
  GroupLabel: ComboboxGroupLabel,
  Collection: ComboboxCollection,
  Empty: ComboboxEmpty,
});
