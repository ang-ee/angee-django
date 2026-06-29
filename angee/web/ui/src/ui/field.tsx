import * as React from "react";
import { Field as BaseField } from "@base-ui/react/field";
import type {
  FieldControlProps as BaseFieldControlProps,
  FieldDescriptionProps as BaseFieldDescriptionProps,
  FieldErrorProps as BaseFieldErrorProps,
  FieldLabelProps as BaseFieldLabelProps,
  FieldRootProps as BaseFieldRootProps,
} from "@base-ui/react/field";

import { cn } from "../lib/cn";
import { toneText } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";
import { OptionalHint, RequiredMark } from "./label";
import { widgetControlSurface } from "./widget-control";

export const fieldVariants = tv({
  slots: {
    root: "grid min-w-0 gap-1.5",
    label: "text-13 font-medium text-fg data-[disabled]:opacity-60",
    description: "text-xs leading-5 text-fg-muted",
    error: "text-xs leading-5 text-danger-text",
    // The box chrome on `control`/`controlFrame` is composed from
    // `widgetControlSurface` in their components; the slots keep layout plus the
    // control's data-attribute-driven invalid tail (a different mechanism from
    // the prop-driven `invalid` variant, which only tints the label here).
    control:
      "w-full text-fg placeholder:text-fg-subtle data-[invalid]:border-danger data-[invalid]:focus:border-danger data-[invalid]:focus:focus-ring-danger",
    controlFrame: "flex min-w-0 items-center rounded-6",
    item: "flex min-w-0 items-start gap-2",
  },
  variants: {
    layout: {
      stack: "",
      inline: {
        root: "sm:grid-cols-3 sm:items-start",
        label: "sm:pt-1.5",
        description: "sm:col-span-2 sm:col-start-2",
        error: "sm:col-span-2 sm:col-start-2",
        control: "sm:col-span-2",
        controlFrame: "sm:col-span-2",
      },
      row: {
        root: "flex items-start justify-between gap-3",
      },
    },
    size: {
      sm: {
        root: "gap-1",
        label: "text-xs",
        description: "text-2xs",
        error: "text-2xs",
        control: "h-btn-sm px-2 text-xs",
        controlFrame: "min-h-btn-sm px-2 py-1 text-xs",
      },
      md: {
        control: "h-input-h px-2 text-13",
        controlFrame: "min-h-input-h px-2 py-1.5 text-13",
      },
      lg: {
        root: "gap-2",
        label: "text-sm",
        description: "text-13",
        error: "text-13",
        control: "h-input-h-lg px-3 text-sm",
        controlFrame: "min-h-input-h-lg px-3 py-2 text-sm",
      },
    },
    invalid: {
      true: {
        label: toneText("danger"),
      },
      false: "",
    },
  },
  defaultVariants: {
    layout: "stack",
    size: "md",
    invalid: false,
  },
});

export type FieldRecipeProps = VariantProps<typeof fieldVariants>;
export type FieldSize = NonNullable<FieldRecipeProps["size"]>;
export type FieldLayout = NonNullable<FieldRecipeProps["layout"]>;

export type FieldRootProps = Omit<BaseFieldRootProps, "className"> &
  Pick<FieldRecipeProps, "layout" | "size" | "invalid"> & {
    className?: string;
  };

const FieldRootBase = React.forwardRef<HTMLDivElement, FieldRootProps>(
  function FieldRoot(
    { className, invalid = false, layout = "stack", size = "md", ...props },
    ref,
  ) {
    const styles = fieldVariants({ invalid, layout, size });
    return (
      <BaseField.Root
        ref={ref}
        className={styles.root({ className })}
        data-layout={layout}
        invalid={invalid}
        {...props}
      />
    );
  },
);
FieldRootBase.displayName = "FieldRoot";

export type FieldLabelProps = Omit<BaseFieldLabelProps, "className"> &
  Pick<FieldRecipeProps, "size"> & {
    className?: string;
    optional?: React.ReactNode;
    required?: boolean;
    requiredIndicator?: React.ReactNode;
  };

export const FieldLabel = React.forwardRef<HTMLLabelElement, FieldLabelProps>(
  function FieldLabel(
    {
      children,
      className,
      optional,
      required = false,
      requiredIndicator = "*",
      size = "md",
      ...props
    },
    ref,
  ) {
    const styles = fieldVariants({ size });
    return (
      <BaseField.Label
        ref={ref}
        className={styles.label({ className })}
        {...props}
      >
        {children}
        <RequiredMark
          required={required}
          indicator={requiredIndicator}
          className="ml-1"
        />
        <OptionalHint optional={optional} className="ml-1" />
      </BaseField.Label>
    );
  },
);
FieldLabel.displayName = "FieldLabel";

export type FieldDescriptionProps = Omit<
  BaseFieldDescriptionProps,
  "className"
> &
  Pick<FieldRecipeProps, "size"> & {
    className?: string;
  };

export const FieldDescription = React.forwardRef<
  HTMLParagraphElement,
  FieldDescriptionProps
>(function FieldDescription({ className, size = "md", ...props }, ref) {
  const styles = fieldVariants({ size });
  return (
    <BaseField.Description
      ref={ref}
      className={styles.description({ className })}
      {...props}
    />
  );
});
FieldDescription.displayName = "FieldDescription";

export type FieldErrorProps = Omit<BaseFieldErrorProps, "className"> &
  Pick<FieldRecipeProps, "size"> & {
    className?: string;
  };

export const FieldError = React.forwardRef<HTMLDivElement, FieldErrorProps>(
  function FieldError({ className, size = "md", ...props }, ref) {
    const styles = fieldVariants({ size });
    return (
      <BaseField.Error
        ref={ref}
        className={styles.error({ className })}
        {...props}
      />
    );
  },
);
FieldError.displayName = "FieldError";

export type FieldControlProps = Omit<
  BaseFieldControlProps,
  "className" | "size"
> &
  Pick<FieldRecipeProps, "size"> & {
    className?: string;
  };

export const FieldControl = React.forwardRef<
  HTMLInputElement,
  FieldControlProps
>(function FieldControl({ className, size = "md", ...props }, ref) {
  const styles = fieldVariants({ size });
  const controlClass = widgetControlSurface({
    focus: "self",
    surface: "sheet",
    disabled: "pseudo",
  });
  return (
    <BaseField.Control
      ref={ref}
      className={styles.control({ className: cn(controlClass, className) })}
      {...props}
    />
  );
});
FieldControl.displayName = "FieldControl";

export type FieldControlFrameProps = React.HTMLAttributes<HTMLDivElement> &
  Pick<FieldRecipeProps, "invalid" | "size"> & {
    className?: string;
  };

export const FieldControlFrame = React.forwardRef<
  HTMLDivElement,
  FieldControlFrameProps
>(function FieldControlFrame(
  { className, invalid = false, size = "md", ...props },
  ref,
) {
  const styles = fieldVariants({ invalid, size });
  const frameClass = widgetControlSurface({
    focus: "within",
    surface: "sheet",
    invalid,
    disabled: "none",
  });
  return (
    <div
      ref={ref}
      className={styles.controlFrame({ className: cn(frameClass, className) })}
      data-invalid={invalid ? "" : undefined}
      {...props}
    />
  );
});
FieldControlFrame.displayName = "FieldControlFrame";

export type FieldItemProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> &
  Pick<FieldRecipeProps, "size"> & {
    className?: string;
    disabled?: boolean;
  };

export const FieldItem = React.forwardRef<HTMLDivElement, FieldItemProps>(
  function FieldItem({ className, size = "md", ...props }, ref) {
    const styles = fieldVariants({ size });
    return (
      <BaseField.Item
        ref={ref}
        className={styles.item({ className })}
        {...props}
      />
    );
  },
);
FieldItem.displayName = "FieldItem";

export const FieldRoot = Object.assign(FieldRootBase, {
  Label: FieldLabel,
  Description: FieldDescription,
  Error: FieldError,
  Control: FieldControl,
  ControlFrame: FieldControlFrame,
  Item: FieldItem,
});
