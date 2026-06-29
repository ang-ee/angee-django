import * as React from "react";
import { Radio as BaseRadio } from "@base-ui/react/radio";
import type {
  RadioIndicatorProps as BaseRadioIndicatorProps,
  RadioRootProps as BaseRadioRootProps,
} from "@base-ui/react/radio";
import {
  RadioGroup as BaseRadioGroup,
  type RadioGroupProps as BaseRadioGroupRootProps,
} from "@base-ui/react/radio-group";

import { tv, type VariantProps } from "../lib/variants";

export const radioGroupVariants = tv({
  slots: {
    root: "min-w-0",
    item:
      "flex min-w-0 cursor-pointer items-start gap-2 rounded-6 outline-none transition-colors focus-within:focus-ring",
    radio:
      "grid size-4 shrink-0 place-content-center rounded-full border border-border-strong bg-sheet text-brand outline-none transition-colors focus-visible:focus-ring data-[checked]:border-brand data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60",
    indicator: "size-2 rounded-full bg-brand",
    text: "min-w-0 space-y-0.5",
    label: "block text-13 font-medium text-fg",
    description: "block text-xs leading-5 text-fg-muted",
    media: "min-w-0",
  },
  variants: {
    orientation: {
      vertical: { root: "grid gap-2" },
      horizontal: { root: "flex flex-wrap gap-2" },
    },
    variant: {
      default: {
        item: "px-2 py-1.5 hover:bg-inset",
      },
      card: {
        item:
          "grid rounded-6 border border-border-subtle bg-sheet p-2 hover:border-border-strong",
      },
    },
    size: {
      sm: {
        radio: "size-3.5",
        indicator: "size-1.5",
        label: "text-xs",
        description: "text-2xs",
      },
      md: "",
      lg: {
        radio: "size-5",
        indicator: "size-2.5",
        label: "text-sm",
        description: "text-13",
      },
    },
  },
  defaultVariants: {
    orientation: "vertical",
    variant: "default",
    size: "md",
  },
});

export type RadioGroupRecipeProps = VariantProps<typeof radioGroupVariants>;
export type RadioGroupVariant = NonNullable<RadioGroupRecipeProps["variant"]>;
export type RadioGroupSize = NonNullable<RadioGroupRecipeProps["size"]>;
export type RadioGroupOrientation = NonNullable<
  RadioGroupRecipeProps["orientation"]
>;

export type RadioGroupRootProps = Omit<
  BaseRadioGroupRootProps,
  "className"
> &
  Pick<RadioGroupRecipeProps, "orientation" | "size" | "variant"> & {
    className?: string;
  };

export const RadioGroupRoot = React.forwardRef<
  HTMLDivElement,
  RadioGroupRootProps
>(function RadioGroupRoot(
  {
    className,
    orientation = "vertical",
    size = "md",
    variant = "default",
    ...props
  },
  ref,
) {
  const styles = radioGroupVariants({ orientation, size, variant });
  return (
    <BaseRadioGroup
      ref={ref}
      aria-orientation={orientation}
      className={styles.root({ className })}
      data-orientation={orientation}
      {...props}
    />
  );
});
RadioGroupRoot.displayName = "RadioGroupRoot";

export type RadioGroupRadioProps = Omit<
  BaseRadioRootProps,
  "className" | "children"
> &
  Pick<RadioGroupRecipeProps, "size"> & {
    children?: React.ReactNode;
    className?: string;
  };

export const RadioGroupRadio = React.forwardRef<
  HTMLSpanElement,
  RadioGroupRadioProps
>(function RadioGroupRadio({ children, className, size = "md", ...props }, ref) {
  const styles = radioGroupVariants({ size });
  return (
    <BaseRadio.Root
      ref={ref}
      className={styles.radio({ className })}
      data-size={size}
      {...props}
    >
      {children ?? <RadioGroupIndicator size={size} />}
    </BaseRadio.Root>
  );
});
RadioGroupRadio.displayName = "RadioGroupRadio";

export type RadioGroupIndicatorProps = Omit<
  BaseRadioIndicatorProps,
  "className"
> &
  Pick<RadioGroupRecipeProps, "size"> & {
    className?: string;
  };

export const RadioGroupIndicator = React.forwardRef<
  HTMLSpanElement,
  RadioGroupIndicatorProps
>(function RadioGroupIndicator({ className, size = "md", ...props }, ref) {
  const styles = radioGroupVariants({ size });
  return (
    <BaseRadio.Indicator
      ref={ref}
      className={styles.indicator({ className })}
      {...props}
    />
  );
});
RadioGroupIndicator.displayName = "RadioGroupIndicator";

export type RadioGroupItemProps = Omit<
  React.LabelHTMLAttributes<HTMLLabelElement>,
  "className" | "color" | "onChange"
> &
  Pick<RadioGroupRecipeProps, "size" | "variant"> & {
    className?: string;
    description?: React.ReactNode;
    disabled?: boolean;
    indicatorClassName?: string;
    label: React.ReactNode;
    radioClassName?: string;
    readOnly?: boolean;
    required?: boolean;
    value: BaseRadioRootProps["value"];
  };

export const RadioGroupItem = React.forwardRef<
  HTMLLabelElement,
  RadioGroupItemProps
>(function RadioGroupItem(
  {
    children,
    className,
    description,
    disabled,
    indicatorClassName,
    label,
    radioClassName,
    readOnly,
    required,
    size = "md",
    value,
    variant = "default",
    ...props
  },
  ref,
) {
  const styles = radioGroupVariants({ size, variant });
  const radio = (
    <RadioGroupRadio
      value={value}
      disabled={disabled}
      readOnly={readOnly}
      required={required}
      size={size}
      className={radioClassName}
    >
      <RadioGroupIndicator size={size} className={indicatorClassName} />
    </RadioGroupRadio>
  );
  const copy = (
    <span className={styles.text()}>
      <span className={styles.label()}>{label}</span>
      {description ? (
        <span className={styles.description()}>{description}</span>
      ) : null}
    </span>
  );

  return (
    <label ref={ref} className={styles.item({ className })} {...props}>
      {variant === "card" ? (
        <>
          {children ? <span className={styles.media()}>{children}</span> : null}
          <span className="flex min-w-0 items-center justify-between gap-2">
            {copy}
            {radio}
          </span>
        </>
      ) : (
        <>
          {radio}
          {copy}
          {children}
        </>
      )}
    </label>
  );
});
RadioGroupItem.displayName = "RadioGroupItem";

export const RadioGroup = Object.assign(RadioGroupRoot, {
  Root: RadioGroupRoot,
  Radio: RadioGroupRadio,
  Indicator: RadioGroupIndicator,
  Item: RadioGroupItem,
});
