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
import { formLayoutVariants, gridCellStyle, type FormFieldSpan } from "./form-layout";
import { sectionEyebrowVariants } from "./section-eyebrow";
import { widgetControlSurface } from "./widget-control";

/**
 * @kind token
 */
export const fieldVariants = tv({
  slots: {
    root: "grid min-w-0 gap-1.5",
    label: "text-13 font-medium text-fg data-[disabled]:opacity-60",
    description: "text-12 leading-5 text-fg-muted",
    error: "text-12 leading-5 text-danger-text",
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
        label: "text-12",
        description: "text-2xs",
        error: "text-2xs",
        control: "h-btn-sm px-2 text-12",
        controlFrame: "min-h-btn-sm px-2 py-1 text-12",
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
    // The form sheet labels its fields with the section eyebrow; dialogs and
    // settings keep the standard label. One label owner, two intents.
    variant: {
      standard: "",
      eyebrow: {
        label: sectionEyebrowVariants({
          size: "sm",
          spacing: "field",
          weight: "medium",
          className: "flex min-h-4 items-center gap-2",
        }),
      },
    },
  },
  defaultVariants: {
    layout: "stack",
    size: "md",
    invalid: false,
    variant: "standard",
  },
});

export type FieldRecipeProps = VariantProps<typeof fieldVariants>;

export type FieldRootProps = Omit<BaseFieldRootProps, "className"> &
  Pick<FieldRecipeProps, "layout" | "size" | "invalid"> & {
    className?: string;
    /** Columns the field spans inside a `FormGrid`. */
    span?: FormFieldSpan;
    /** The named `FormGrid` area the field occupies. */
    area?: string;
  };

const FieldRootBase = React.forwardRef<HTMLDivElement, FieldRootProps>(
  function FieldRoot(
    { area, className, invalid = false, layout = "stack", size = "md", span, style, ...props },
    ref,
  ) {
    const styles = fieldVariants({ invalid, layout, size });
    return (
      <BaseField.Root
        ref={ref}
        className={styles.root({
          className: cn(span ? formLayoutVariants({ span }).fieldRow() : undefined, className),
        })}
        style={{ ...gridCellStyle(area), ...style }}
        data-layout={layout}
        invalid={invalid}
        {...props}
      />
    );
  },
);

export interface RequiredMarkProps {
  required?: boolean;
  /** The mark glyph/text (defaults to `*`). */
  indicator?: React.ReactNode;
  /** Extra classes — pass `ml-1` where the parent has no gap. */
  className?: string;
}

/**
 * The decorative required-field mark — `aria-hidden`, since the accessible
 * required state lives on the control, not the label. The one owner `FieldLabel`
 * and the editable-lines header compose.
 * @owner FieldLabel
 */
export function RequiredMark({
  required = false,
  indicator = "*",
  className,
}: RequiredMarkProps): React.ReactElement | null {
  if (!required) return null;
  return (
    <span className={cn(toneText("danger"), className)} aria-hidden="true">
      {indicator}
    </span>
  );
}

export interface OptionalHintProps {
  /** The hint content (e.g. "optional"); nothing renders when absent. */
  optional?: React.ReactNode;
  /** Extra classes — pass `ml-1` where the parent has no gap. */
  className?: string;
}

/** The muted hint shown after a label for an optional field.
 * @owner FieldLabel
 */
export function OptionalHint({
  optional,
  className,
}: OptionalHintProps): React.ReactElement | null {
  if (!optional) return null;
  return (
    <span className={cn("font-normal text-fg-muted", className)}>{optional}</span>
  );
}
FieldRootBase.displayName = "FieldRoot";

export type FieldLabelProps = Omit<BaseFieldLabelProps, "className"> &
  Pick<FieldRecipeProps, "size" | "variant"> & {
    className?: string;
    /** A compact note at the label's end ("Notifications", a source mark). */
    meta?: React.ReactNode;
    optional?: React.ReactNode;
    required?: boolean;
    requiredIndicator?: React.ReactNode;
  };

/** The one field label: standard in dialogs and settings, the eyebrow on the form sheet.
 * @kind primitive
 */
export const FieldLabel = React.forwardRef<HTMLLabelElement, FieldLabelProps>(
  function FieldLabel(
    {
      children,
      className,
      meta,
      optional,
      required = false,
      requiredIndicator = "*",
      size = "md",
      variant = "standard",
      ...props
    },
    ref,
  ) {
    const styles = fieldVariants({ size, variant });
    return (
      <BaseField.Label
        ref={ref}
        className={styles.label({ className: cn(meta !== undefined && "flex items-center gap-2", className) })}
        {...props}
      >
        {children}
        <RequiredMark
          required={required}
          indicator={requiredIndicator}
          className="ml-1"
        />
        <OptionalHint optional={optional} className="ml-1" />
        {meta !== undefined && meta !== null ? (
          <span className="ml-auto shrink-0 text-2xs font-normal normal-case tracking-normal text-fg-subtle">{meta}</span>
        ) : null}
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

/**
 * @kind primitive
 */
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

/**
 * @kind primitive
 */
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

/**
 * @kind primitive
 */
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

/**
 * @owner FieldControl
 */
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

/**
 * @kind primitive
 */
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

/**
 * @kind primitive
 */
export const FieldRoot = Object.assign(FieldRootBase, {
  Label: FieldLabel,
  Description: FieldDescription,
  Error: FieldError,
  Control: FieldControl,
  ControlFrame: FieldControlFrame,
  Item: FieldItem,
});
