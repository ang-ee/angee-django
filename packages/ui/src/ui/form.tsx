import * as React from "react";
import { Form as BaseForm } from "@base-ui/react/form";
import type {
  FormProps as BaseFormProps,
  FormValidationMode,
} from "@base-ui/react/form";

import { tv, type VariantProps } from "../lib/variants";
import {
  FieldControl,
  FieldControlFrame,
  FieldDescription,
  FieldError,
  FieldItem,
  FieldLabel,
  FieldRoot,
  type FieldDescriptionProps,
  type FieldErrorProps,
  type FieldLabelProps,
  type FieldRootProps,
} from "./field";

export const formVariants = tv({
  base: "min-w-0",
  variants: {
    layout: {
      stack: "grid",
      inline: "flex flex-wrap items-start",
      panel: "grid rounded-6 border border-border-subtle bg-sheet",
      plain: "",
    },
    density: {
      compact: "gap-3",
      comfortable: "gap-4",
      spacious: "gap-6",
    },
  },
  defaultVariants: {
    layout: "stack",
    density: "comfortable",
  },
});

export type FormRecipeProps = VariantProps<typeof formVariants>;
export type FormLayout = NonNullable<FormRecipeProps["layout"]>;
export type FormDensity = NonNullable<FormRecipeProps["density"]>;

export type FormRootProps<
  FormValues extends Record<string, any> = Record<string, any>,
> = Omit<BaseFormProps<FormValues>, "className" | "onFormSubmit"> &
  FormRecipeProps & {
    className?: string;
    onFormSubmit?: BaseFormProps<FormValues>["onFormSubmit"];
    validationMode?: FormValidationMode;
  };

const FormRootBase = React.forwardRef<HTMLFormElement, FormRootProps>(
  function FormRoot(
    {
      className,
      density = "comfortable",
      layout = "stack",
      validationMode = "onSubmit",
      ...props
    },
    ref,
  ) {
    return (
      <BaseForm
        ref={ref}
        className={formVariants({ className, density, layout })}
        data-layout={layout}
        validationMode={validationMode}
        {...props}
      />
    );
  },
);
FormRootBase.displayName = "FormRoot";

export type FormFieldProps = Omit<FieldRootProps, "children"> & {
  children: React.ReactNode;
  description?: React.ReactNode;
  descriptionProps?: Omit<FieldDescriptionProps, "children" | "size">;
  errorProps?: Omit<FieldErrorProps, "size">;
  label?: React.ReactNode;
  labelProps?: Omit<
    FieldLabelProps,
    "children" | "optional" | "required" | "requiredIndicator" | "size"
  >;
  optional?: React.ReactNode;
  required?: boolean;
  requiredIndicator?: React.ReactNode;
};

const FormField = React.forwardRef<HTMLDivElement, FormFieldProps>(
  function FormField(
    {
      children,
      description,
      descriptionProps,
      errorProps,
      label,
      labelProps,
      optional,
      required = false,
      requiredIndicator,
      size = "md",
      ...props
    },
    ref,
  ) {
    return (
      <FieldRoot ref={ref} size={size} {...props}>
        {label ? (
          <FieldLabel
            optional={optional}
            required={required}
            requiredIndicator={requiredIndicator}
            size={size}
            {...labelProps}
          >
            {label}
          </FieldLabel>
        ) : null}
        {children}
        {description ? (
          <FieldDescription size={size} {...descriptionProps}>
            {description}
          </FieldDescription>
        ) : null}
        <FieldError size={size} {...errorProps} />
      </FieldRoot>
    );
  },
);
FormField.displayName = "FormField";

export const FormRoot = Object.assign(FormRootBase, {
  Field: FormField,
  FieldRoot,
  Label: FieldLabel,
  Description: FieldDescription,
  Error: FieldError,
  Control: FieldControl,
  ControlFrame: FieldControlFrame,
  Item: FieldItem,
});
