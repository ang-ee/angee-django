import * as React from "react";
import { NumberField as BaseNumberField } from "@base-ui/react/number-field";
import type {
  NumberFieldDecrementProps as BaseNumberFieldDecrementProps,
  NumberFieldGroupProps as BaseNumberFieldGroupProps,
  NumberFieldIncrementProps as BaseNumberFieldIncrementProps,
  NumberFieldInputProps as BaseNumberFieldInputProps,
  NumberFieldRootProps as BaseNumberFieldRootProps,
  NumberFieldScrubAreaCursorProps as BaseNumberFieldScrubAreaCursorProps,
  NumberFieldScrubAreaProps as BaseNumberFieldScrubAreaProps,
} from "@base-ui/react/number-field";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import {
  WIDGET_CONTROL_DATA_READONLY_CLASS,
  widgetControlSurface,
} from "./widget-control";

export const numberFieldVariants = tv({
  slots: {
    root: "inline-flex min-w-0 flex-col gap-1",
    // Box chrome (border / surface / focus / disabled / invalid / read-only) is
    // composed from `widgetControlSurface` in NumberFieldGroup; the group slot
    // keeps only layout plus the data-attribute read-only path. `invalid` and
    // `readOnly` stay as inert pass-through variants so the prop types resolve.
    group:
      `inline-flex h-input-h w-full min-w-0 items-center overflow-hidden rounded-6 text-fg ${WIDGET_CONTROL_DATA_READONLY_CLASS}`,
    input:
      "min-w-0 flex-1 bg-transparent px-2 text-13 tabular-nums outline-none placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:opacity-60",
    stepper:
      "flex h-full w-7 shrink-0 items-center justify-center border-l border-border-subtle bg-inset text-fg-muted outline-none transition-colors hover:bg-sheet hover:text-fg focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-40 data-[disabled]:cursor-not-allowed data-[disabled]:opacity-40 [&_.glyph]:size-3",
    scrubArea:
      "inline-flex cursor-ew-resize touch-none select-none items-center gap-1 text-2xs font-medium uppercase text-fg-muted data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
    scrubCursor:
      "fixed z-popover inline-flex size-5 items-center justify-center rounded-full border border-border bg-popover text-fg shadow-popover [&_.glyph]:size-3",
  },
  variants: {
    size: {
      sm: {
        group: "h-btn-sm",
        input: "px-2 text-xs",
        stepper: "w-6",
      },
      md: "",
      lg: {
        group: "h-input-h-lg",
        input: "px-3 text-sm",
        stepper: "w-8 [&_.glyph]:size-3.5",
      },
    },
    align: {
      start: { input: "text-left" },
      center: { input: "text-center" },
      end: { input: "text-right" },
    },
    invalid: {
      true: {},
      false: {},
    },
    readOnly: {
      true: {},
      false: {},
    },
  },
  defaultVariants: {
    size: "md",
    align: "end",
    invalid: false,
    readOnly: false,
  },
});

export type NumberFieldRecipeProps = VariantProps<typeof numberFieldVariants>;
export type NumberFieldSize = NonNullable<NumberFieldRecipeProps["size"]>;
export type NumberFieldAlign = NonNullable<NumberFieldRecipeProps["align"]>;

export type NumberFieldRootProps = Omit<BaseNumberFieldRootProps, "className"> &
  Pick<NumberFieldRecipeProps, "size" | "invalid"> & {
    className?: string;
  };

export const NumberFieldRoot = React.forwardRef<
  HTMLDivElement,
  NumberFieldRootProps
>(function NumberFieldRoot(
  { className, invalid = false, size = "md", ...props },
  ref,
) {
  const styles = numberFieldVariants({ invalid, size });
  return (
    <BaseNumberField.Root
      ref={ref}
      aria-invalid={invalid || undefined}
      className={styles.root({ className })}
      {...props}
    />
  );
});
NumberFieldRoot.displayName = "NumberFieldRoot";

export type NumberFieldGroupProps = Omit<
  BaseNumberFieldGroupProps,
  "className"
> &
  Pick<NumberFieldRecipeProps, "invalid" | "readOnly" | "size"> & {
    className?: string;
  };

export const NumberFieldGroup = React.forwardRef<
  HTMLDivElement,
  NumberFieldGroupProps
>(function NumberFieldGroup(
  { className, invalid = false, readOnly = false, size = "md", ...props },
  ref,
) {
  const styles = numberFieldVariants({ invalid, readOnly, size });
  const groupClass = widgetControlSurface({
    focus: "within",
    surface: "sheet",
    invalid,
    readOnly,
    disabled: "data",
  });
  return (
    <BaseNumberField.Group
      ref={ref}
      className={styles.group({ className: cn(groupClass, className) })}
      {...props}
    />
  );
});
NumberFieldGroup.displayName = "NumberFieldGroup";

export type NumberFieldInputProps = Omit<
  BaseNumberFieldInputProps,
  "className" | "size"
> &
  NumberFieldRecipeProps & {
    className?: string;
  };

export const NumberFieldInput = React.forwardRef<
  HTMLInputElement,
  NumberFieldInputProps
>(function NumberFieldInput(
  { align = "end", className, invalid = false, size = "md", ...props },
  ref,
) {
  const styles = numberFieldVariants({ align, invalid, size });
  return (
    <BaseNumberField.Input
      ref={ref}
      className={styles.input({ className })}
      {...props}
    />
  );
});
NumberFieldInput.displayName = "NumberFieldInput";

export type NumberFieldIncrementProps = Omit<
  BaseNumberFieldIncrementProps,
  "className"
> &
  Pick<NumberFieldRecipeProps, "size"> & {
    className?: string;
  };

export const NumberFieldIncrement = React.forwardRef<
  HTMLButtonElement,
  NumberFieldIncrementProps
>(function NumberFieldIncrement(
  {
    children = <Glyph name="plus" />,
    className,
    size = "md",
    ...props
  },
  ref,
) {
  const t = useBaseT();
  const styles = numberFieldVariants({ size });
  return (
    <BaseNumberField.Increment
      ref={ref}
      aria-label={t("numberField.increment")}
      className={styles.stepper({ className })}
      {...props}
    >
      {children}
    </BaseNumberField.Increment>
  );
});
NumberFieldIncrement.displayName = "NumberFieldIncrement";

export type NumberFieldDecrementProps = Omit<
  BaseNumberFieldDecrementProps,
  "className"
> &
  Pick<NumberFieldRecipeProps, "size"> & {
    className?: string;
  };

export const NumberFieldDecrement = React.forwardRef<
  HTMLButtonElement,
  NumberFieldDecrementProps
>(function NumberFieldDecrement(
  {
    children = <Glyph name="minus" />,
    className,
    size = "md",
    ...props
  },
  ref,
) {
  const t = useBaseT();
  const styles = numberFieldVariants({ size });
  return (
    <BaseNumberField.Decrement
      ref={ref}
      aria-label={t("numberField.decrement")}
      className={styles.stepper({ className })}
      {...props}
    >
      {children}
    </BaseNumberField.Decrement>
  );
});
NumberFieldDecrement.displayName = "NumberFieldDecrement";

export type NumberFieldScrubAreaProps = Omit<
  BaseNumberFieldScrubAreaProps,
  "className"
> & {
  className?: string;
};

export const NumberFieldScrubArea = React.forwardRef<
  HTMLSpanElement,
  NumberFieldScrubAreaProps
>(function NumberFieldScrubArea({ className, ...props }, ref) {
  const styles = numberFieldVariants();
  return (
    <BaseNumberField.ScrubArea
      ref={ref}
      className={styles.scrubArea({ className })}
      {...props}
    />
  );
});
NumberFieldScrubArea.displayName = "NumberFieldScrubArea";

export type NumberFieldScrubAreaCursorProps = Omit<
  BaseNumberFieldScrubAreaCursorProps,
  "className"
> & {
  className?: string;
};

export const NumberFieldScrubAreaCursor = React.forwardRef<
  HTMLSpanElement,
  NumberFieldScrubAreaCursorProps
>(function NumberFieldScrubAreaCursor(
  {
    children = (
      <>
        <Glyph name="chevron-down" />
        <Glyph name="chevron-up" />
      </>
    ),
    className,
    ...props
  },
  ref,
) {
  const styles = numberFieldVariants();
  return (
    <BaseNumberField.ScrubAreaCursor
      ref={ref}
      className={styles.scrubCursor({ className })}
      {...props}
    >
      {children}
    </BaseNumberField.ScrubAreaCursor>
  );
});
NumberFieldScrubAreaCursor.displayName = "NumberFieldScrubAreaCursor";

export type NumberFieldProps = Omit<NumberFieldRootProps, "children"> &
  Pick<NumberFieldRecipeProps, "align"> & {
    decrementClassName?: string;
    decrementLabel?: string;
    groupClassName?: string;
    incrementClassName?: string;
    incrementLabel?: string;
    inputClassName?: string;
    inputProps?: Omit<
      NumberFieldInputProps,
      "align" | "className" | "invalid" | "size"
    > & {
      className?: string;
    };
    showStepper?: boolean;
  };

export const NumberField = React.forwardRef<HTMLDivElement, NumberFieldProps>(
  function NumberField(
    {
      align = "end",
      className,
      decrementClassName,
      decrementLabel,
      groupClassName,
      incrementClassName,
      incrementLabel,
      inputClassName,
      inputProps,
      invalid = false,
      readOnly = false,
      showStepper = true,
      size = "md",
      ...props
    },
    ref,
  ) {
    const t = useBaseT();
    const { className: inputPropsClassName, ...restInputProps } =
      inputProps ?? {};

    return (
      <NumberFieldRoot
        ref={ref}
        className={className}
        invalid={invalid}
        readOnly={readOnly}
        size={size}
        {...props}
      >
        <NumberFieldGroup
          className={groupClassName}
          invalid={invalid}
          readOnly={readOnly}
          size={size}
        >
          <NumberFieldInput
            align={align}
            className={inputPropsClassName ?? inputClassName}
            invalid={invalid}
            size={size}
            {...restInputProps}
          />
          {showStepper ? (
            <>
              <NumberFieldDecrement
                aria-label={decrementLabel ?? t("numberField.decrement")}
                className={decrementClassName}
                size={size}
              />
              <NumberFieldIncrement
                aria-label={incrementLabel ?? t("numberField.increment")}
                className={incrementClassName}
                size={size}
              />
            </>
          ) : null}
        </NumberFieldGroup>
      </NumberFieldRoot>
    );
  },
);
NumberField.displayName = "NumberField";
