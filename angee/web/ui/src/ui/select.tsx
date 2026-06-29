import * as React from "react";
import { Select as BaseSelect } from "@base-ui/react/select";
import type {
  SelectArrowProps as BaseSelectArrowProps,
  SelectBackdropProps as BaseSelectBackdropProps,
  SelectGroupLabelProps as BaseSelectGroupLabelProps,
  SelectGroupProps as BaseSelectGroupProps,
  SelectIconProps as BaseSelectIconProps,
  SelectItemIndicatorProps as BaseSelectItemIndicatorProps,
  SelectItemProps as BaseSelectItemProps,
  SelectItemTextProps as BaseSelectItemTextProps,
  SelectListProps as BaseSelectListProps,
  SelectPopupProps as BaseSelectPopupProps,
  SelectPortalProps as BaseSelectPortalProps,
  SelectPositionerProps as BaseSelectPositionerProps,
  SelectRootChangeEventDetails,
  SelectRootProps as BaseSelectRootProps,
  SelectTriggerProps as BaseSelectTriggerProps,
  SelectValueProps as BaseSelectValueProps,
} from "@base-ui/react/select";
import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { POPUP_BASE } from "./popover";
import { widgetControlSurface } from "./widget-control";

export const selectVariants = tv({
  slots: {
    // Box chrome (border / surface / focus / disabled / invalid / read-only) is
    // composed from `widgetControlSurface` in SelectTrigger; the trigger slot
    // keeps only layout. `invalid` and `readOnly` stay as inert pass-through
    // variants so the prop types resolve.
    trigger:
      "inline-flex h-9 w-full min-w-0 cursor-pointer items-center justify-between gap-2 rounded-6 px-2 text-left text-13 text-fg",
    value: "min-w-0 flex-1 truncate text-left",
    icon:
      "ml-auto flex size-4 shrink-0 items-center justify-center text-fg-muted transition-transform data-[open]:rotate-180 [&_svg]:size-3.5",
    content: `${POPUP_BASE} min-w-[var(--anchor-width)]`,
    list: "max-h-72 overflow-y-auto p-1",
    item:
      "relative flex h-8 cursor-pointer select-none items-center gap-2 rounded-6 px-2 pr-8 text-13 text-fg outline-none data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[highlighted]:bg-inset",
    itemText: "min-w-0 flex-1 truncate",
    indicator:
      "absolute right-2 flex size-3.5 items-center justify-center text-brand [&_svg]:size-3.5",
    label: "px-2 py-1.5 text-2xs font-semibold uppercase text-fg-muted",
    separator: "-mx-1 my-1 h-px bg-border-subtle",
  },
  variants: {
    size: {
      sm: {
        trigger: "h-7 text-13",
        content: "text-13",
        item: "h-7",
      },
      md: {
        trigger: "h-9",
        content: "",
        item: "h-8",
      },
    },
    invalid: {
      true: {},
      false: {},
    },
    readOnly: {
      true: {},
      false: {},
    },
    inset: {
      true: { item: "pl-8" },
      false: { item: "" },
    },
  },
  defaultVariants: {
    size: "md",
    invalid: false,
    readOnly: false,
    inset: false,
  },
});

type SelectRecipeProps = VariantProps<typeof selectVariants>;

export type SelectSize = NonNullable<SelectRecipeProps["size"]>;

export interface SelectChoice {
  value: string;
  label: React.ReactNode;
  disabled?: boolean;
}

export type SelectRootProps<
  Value = string,
  Multiple extends boolean | undefined = false,
> = BaseSelectRootProps<Value, Multiple>;
export type SelectPortalProps = BaseSelectPortalProps;
export type SelectPositionerProps = BaseSelectPositionerProps;
export type SelectBackdropProps = BaseSelectBackdropProps;
export type SelectArrowProps = BaseSelectArrowProps;
export type SelectGroupProps = BaseSelectGroupProps;

export const SelectRoot = BaseSelect.Root;
export const SelectPortal = BaseSelect.Portal;
export const SelectPositioner = BaseSelect.Positioner;
export const SelectBackdrop = BaseSelect.Backdrop;
export const SelectArrow = BaseSelect.Arrow;
export const SelectGroup = BaseSelect.Group;

export type SelectTriggerProps = Omit<BaseSelectTriggerProps, "className"> &
  Pick<SelectRecipeProps, "size" | "invalid" | "readOnly"> & {
    className?: string;
  };

export const SelectTrigger = React.forwardRef<
  HTMLButtonElement,
  SelectTriggerProps
>(function SelectTrigger(
  { className, size = "md", invalid = false, readOnly = false, ...props },
  ref,
) {
  const styles = selectVariants({ size, invalid, readOnly });
  // Re-assert the trigger's bare `rounded-6` after the owner chrome (whose base is
  // `rounded-6`); the radius-unification stage converges both to one token.
  const triggerClass = cn(
    widgetControlSurface({
      focus: "visible",
      surface: "inset",
      invalid,
      readOnly,
      disabled: "data",
    }),
    "rounded-6",
  );
  return (
    <BaseSelect.Trigger
      ref={ref}
      className={styles.trigger({ className: cn(triggerClass, className) })}
      {...props}
    />
  );
});
SelectTrigger.displayName = "SelectTrigger";

export type SelectValueProps = Omit<BaseSelectValueProps, "className"> & {
  className?: string;
};

export const SelectValue = React.forwardRef<
  HTMLSpanElement,
  SelectValueProps
>(function SelectValue({ className, ...props }, ref) {
  const styles = selectVariants();
  return (
    <BaseSelect.Value
      ref={ref}
      className={styles.value({ className })}
      {...props}
    />
  );
});
SelectValue.displayName = "SelectValue";

export type SelectIconProps = Omit<BaseSelectIconProps, "className"> & {
  className?: string;
};

export const SelectIcon = React.forwardRef<
  HTMLSpanElement,
  SelectIconProps
>(function SelectIcon(
  { className, children = <Glyph name="chevron-down" />, ...props },
  ref,
) {
  const styles = selectVariants();
  return (
    <BaseSelect.Icon
      ref={ref}
      className={styles.icon({ className })}
      {...props}
    >
      {children}
    </BaseSelect.Icon>
  );
});
SelectIcon.displayName = "SelectIcon";

export type SelectContentProps = Omit<BaseSelectPopupProps, "className"> &
  Pick<SelectRecipeProps, "size"> & {
    className?: string;
  };

export const SelectContent = React.forwardRef<
  HTMLDivElement,
  SelectContentProps
>(function SelectContent({ className, size = "md", ...props }, ref) {
  const styles = selectVariants({ size });
  return (
    <BaseSelect.Popup
      ref={ref}
      className={styles.content({ className })}
      {...props}
    />
  );
});
SelectContent.displayName = "SelectContent";

export type SelectListProps = Omit<BaseSelectListProps, "className"> & {
  className?: string;
};

export const SelectList = React.forwardRef<
  HTMLDivElement,
  SelectListProps
>(function SelectList({ className, ...props }, ref) {
  const styles = selectVariants();
  return (
    <BaseSelect.List
      ref={ref}
      className={styles.list({ className })}
      {...props}
    />
  );
});
SelectList.displayName = "SelectList";

export type SelectItemProps = Omit<BaseSelectItemProps, "className"> &
  Pick<SelectRecipeProps, "inset" | "size"> & {
    className?: string;
  };

export const SelectItem = React.forwardRef<
  HTMLDivElement,
  SelectItemProps
>(function SelectItem(
  { className, inset = false, size = "md", ...props },
  ref,
) {
  const styles = selectVariants({ inset, size });
  return (
    <BaseSelect.Item
      ref={ref}
      className={styles.item({ className })}
      {...props}
    />
  );
});
SelectItem.displayName = "SelectItem";

export type SelectItemTextProps = Omit<
  BaseSelectItemTextProps,
  "className"
> & {
  className?: string;
};

export const SelectItemText = React.forwardRef<
  HTMLDivElement,
  SelectItemTextProps
>(function SelectItemText({ className, ...props }, ref) {
  const styles = selectVariants();
  return (
    <BaseSelect.ItemText
      ref={ref}
      className={styles.itemText({ className })}
      {...props}
    />
  );
});
SelectItemText.displayName = "SelectItemText";

export type SelectItemIndicatorProps = Omit<
  BaseSelectItemIndicatorProps,
  "className"
> & {
  className?: string;
};

export const SelectItemIndicator = React.forwardRef<
  HTMLSpanElement,
  SelectItemIndicatorProps
>(function SelectItemIndicator(
  { className, children = <Glyph name="check" />, ...props },
  ref,
) {
  const styles = selectVariants();
  return (
    <BaseSelect.ItemIndicator
      ref={ref}
      className={styles.indicator({ className })}
      {...props}
    >
      {children}
    </BaseSelect.ItemIndicator>
  );
});
SelectItemIndicator.displayName = "SelectItemIndicator";

export type SelectLabelProps = Omit<BaseSelectGroupLabelProps, "className"> & {
  className?: string;
};

export const SelectLabel = React.forwardRef<
  HTMLDivElement,
  SelectLabelProps
>(function SelectLabel({ className, ...props }, ref) {
  const styles = selectVariants();
  return (
    <BaseSelect.GroupLabel
      ref={ref}
      className={styles.label({ className })}
      {...props}
    />
  );
});
SelectLabel.displayName = "SelectLabel";

export type SelectSeparatorProps = React.ComponentPropsWithoutRef<
  typeof BaseSelect.Separator
> & {
  className?: string;
};

export const SelectSeparator = React.forwardRef<
  HTMLDivElement,
  SelectSeparatorProps
>(function SelectSeparator({ className, ...props }, ref) {
  const styles = selectVariants();
  return (
    <BaseSelect.Separator
      ref={ref}
      className={styles.separator({ className })}
      {...props}
    />
  );
});
SelectSeparator.displayName = "SelectSeparator";

export type SelectProps = Omit<
  BaseSelectRootProps<string, false>,
  "children" | "items" | "value" | "defaultValue" | "onValueChange"
> &
  Pick<SelectRecipeProps, "size" | "invalid"> & {
    options?: readonly SelectChoice[];
    placeholder?: React.ReactNode;
    value?: string;
    defaultValue?: string;
    className?: string;
    contentClassName?: string;
    "aria-label"?: string;
    "aria-labelledby"?: string;
    onValueChange?: (
      value: string,
      eventDetails: SelectRootChangeEventDetails,
    ) => void;
  };

export const Select = function Select({
  options = [],
  placeholder,
  value,
  defaultValue,
  className,
  contentClassName,
  size = "md",
  invalid = false,
  readOnly = false,
  "aria-label": ariaLabel,
  "aria-labelledby": ariaLabelledBy,
  onValueChange,
  ...props
}: SelectProps) {
  const labels = React.useMemo(
    () => new Map(options.map((option) => [option.value, option.label])),
    [options],
  );

  function handleValueChange(
    nextValue: string | null,
    eventDetails: SelectRootChangeEventDetails,
  ) {
    onValueChange?.(nextValue ?? "", eventDetails);
  }

  return (
    <SelectRoot
      items={options}
      value={value}
      defaultValue={defaultValue}
      onValueChange={handleValueChange}
      readOnly={readOnly}
      {...props}
    >
      <SelectTrigger
        size={size}
        invalid={invalid}
        readOnly={readOnly}
        className={className}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
      >
        <SelectValue>
          {(selected) => selectedLabel(selected, labels, placeholder)}
        </SelectValue>
        <SelectIcon />
      </SelectTrigger>
      <SelectPortal>
        <SelectPositioner sideOffset={4}>
          <SelectContent size={size} className={contentClassName}>
            <SelectList>
              {options.map((option) => (
                <SelectItem
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                  label={
                    typeof option.label === "string"
                      ? option.label
                      : undefined
                  }
                  size={size}
                >
                  <SelectItemText>{option.label}</SelectItemText>
                  <SelectItemIndicator />
                </SelectItem>
              ))}
            </SelectList>
          </SelectContent>
        </SelectPositioner>
      </SelectPortal>
    </SelectRoot>
  );
};

export const SelectPrimitive = {
  Root: SelectRoot,
  Trigger: SelectTrigger,
  Value: SelectValue,
  Icon: SelectIcon,
  Portal: SelectPortal,
  Positioner: SelectPositioner,
  Content: SelectContent,
  List: SelectList,
  Item: SelectItem,
  ItemIndicator: SelectItemIndicator,
  ItemText: SelectItemText,
  Label: SelectLabel,
  Separator: SelectSeparator,
  Arrow: SelectArrow,
  Backdrop: SelectBackdrop,
  Group: SelectGroup,
};

function selectedLabel(
  selected: unknown,
  labels: ReadonlyMap<string, React.ReactNode>,
  placeholder: React.ReactNode,
): React.ReactNode {
  const selectedKey = selected == null ? "" : String(selected);
  return labels.get(selectedKey) ?? placeholder ?? selectedKey;
}
