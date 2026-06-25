import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import {
  WIDGET_CONTROL_DATA_READONLY_CLASS,
  WIDGET_CONTROL_READONLY_CLASS,
  widgetControlSurfaceVariants,
} from "./widget-control";

export const inputSurfaceVariants = widgetControlSurfaceVariants;

export const inputTriggerVariants = tv({
  base: "flex h-input-h w-full cursor-pointer items-center justify-between gap-2 rounded-md border border-border bg-sheet px-2 text-left text-13 text-fg outline-none transition-colors hover:border-border-strong focus-visible:border-border-focus focus-visible:focus-ring data-[popup-open]:border-border-focus",
  variants: {
    readOnly: {
      true: WIDGET_CONTROL_READONLY_CLASS,
      false: "",
    },
  },
  defaultVariants: {
    readOnly: false,
  },
});

export const INPUT_SHELL = inputSurfaceVariants({ focus: "self" });
export const INPUT_SHELL_WITHIN = inputSurfaceVariants({ focus: "within" });
export const INPUT_BASE =
  `w-full h-input-h px-2 text-13 text-fg ${INPUT_SHELL}`;
export const INPUT_READONLY = WIDGET_CONTROL_READONLY_CLASS;
export const INPUT_NUMERIC = "tabular-nums text-right";
export const TRIGGER_BASE = inputTriggerVariants();

export const inputVariants = tv({
  base: `w-full text-fg transition-colors placeholder:text-fg-subtle disabled:cursor-not-allowed disabled:bg-inset disabled:opacity-60 ${INPUT_SHELL}`,
  variants: {
    size: {
      sm: "h-btn-sm px-2 text-xs",
      md: "h-input-h px-2 text-13",
      lg: "h-input-h-lg px-3 text-sm",
    },
    invalid: {
      true: "border-danger focus:border-danger focus:focus-ring-danger",
      false: "",
    },
    readOnly: {
      true: WIDGET_CONTROL_READONLY_CLASS,
      false: "",
    },
  },
  defaultVariants: {
    size: "md",
    invalid: false,
    readOnly: false,
  },
});

export const searchInputVariants = tv({
  slots: {
    root:
      `inline-flex w-full min-w-0 items-center overflow-hidden rounded-md border text-fg outline-none transition-colors focus-within:border-border-focus focus-within:focus-ring data-[disabled]:cursor-not-allowed data-[disabled]:bg-inset data-[disabled]:opacity-60 ${WIDGET_CONTROL_DATA_READONLY_CLASS}`,
    icon: "pointer-events-none shrink-0 text-fg-muted",
    input:
      "min-w-0 flex-1 border-0 bg-transparent text-fg outline-none placeholder:text-fg-muted disabled:cursor-not-allowed",
    clear:
      "grid shrink-0 place-content-center rounded text-fg-muted outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-50",
    clearIcon: "shrink-0",
  },
  variants: {
    size: {
      sm: {
        root: "h-btn-sm gap-1.5 pl-2 pr-1 text-xs",
        icon: "size-3",
        input: "h-btn-sm text-xs",
        clear: "size-5",
        clearIcon: "size-3",
      },
      md: {
        root: "h-input-h gap-2 pl-2 pr-1 text-13",
        icon: "size-3.5",
        input: "h-input-h text-13",
        clear: "size-5",
        clearIcon: "size-3",
      },
      lg: {
        root: "h-input-h-lg gap-2.5 pl-3 pr-1.5 text-sm",
        icon: "size-4",
        input: "h-input-h-lg text-sm",
        clear: "size-6",
        clearIcon: "size-3.5",
      },
    },
    surface: {
      sheet: { root: "border-border bg-sheet hover:border-border-strong" },
      inset: { root: "border-transparent bg-inset focus-within:bg-sheet" },
      plain: { root: "border-transparent bg-transparent" },
    },
    invalid: {
      true: {
        root: "border-danger focus-within:border-danger focus-within:focus-ring-danger",
      },
      false: {},
    },
    readOnly: {
      true: { root: WIDGET_CONTROL_READONLY_CLASS, input: "cursor-default" },
      false: {},
    },
  },
  defaultVariants: {
    size: "md",
    surface: "sheet",
    invalid: false,
    readOnly: false,
  },
});

type InputRecipeProps = VariantProps<typeof inputVariants>;
type SearchInputRecipeProps = VariantProps<typeof searchInputVariants>;

export type InputSize = NonNullable<InputRecipeProps["size"]>;
export type SearchInputSize = NonNullable<SearchInputRecipeProps["size"]>;
export type SearchInputSurface = NonNullable<SearchInputRecipeProps["surface"]>;

export type InputProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "className" | "color" | "size"
> &
  InputRecipeProps & {
    className?: string;
  };

export const Input = React.forwardRef<HTMLInputElement, InputProps>(function Input(
  {
    size = "md",
    invalid = false,
    readOnly = false,
    type = "text",
    className,
    ...props
  },
  ref,
) {
  return (
    <input
      ref={ref}
      type={type}
      readOnly={readOnly}
      aria-invalid={invalid || undefined}
      className={cn(inputVariants({ size, invalid, readOnly }), className)}
      {...props}
    />
  );
});

Input.displayName = "Input";

export type TextInputProps = InputProps;

export function TextInput(props: TextInputProps): React.ReactElement {
  return <Input {...props} />;
}

export type SearchInputProps = Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "className" | "color" | "size" | "type"
> &
  SearchInputRecipeProps & {
    className?: string;
    clearLabel?: string;
    inputClassName?: string;
    onClear?: () => void;
  };

export const SearchInput = React.forwardRef<HTMLInputElement, SearchInputProps>(
  function SearchInput(
    {
      size = "md",
      surface = "sheet",
      invalid = false,
      readOnly = false,
      className,
      clearLabel,
      disabled,
      inputClassName,
      onClear,
      placeholder,
      value,
      ...props
    },
    ref,
  ) {
    const t = useBaseT();
    const styles = searchInputVariants({ size, surface, invalid, readOnly });
    const hasValue =
      typeof value === "string"
        ? value.length > 0
        : value !== undefined && value !== null && String(value).length > 0;

    return (
      <div
        className={styles.root({ className })}
        data-disabled={disabled ? "" : undefined}
        data-readonly={readOnly ? "" : undefined}
      >
        <Glyph name="search" className={styles.icon()} />
        <input
          ref={ref}
          type="search"
          disabled={disabled}
          readOnly={readOnly}
          value={value}
          placeholder={placeholder ?? t("search.placeholder")}
          aria-invalid={invalid || undefined}
          className={styles.input({ className: inputClassName })}
          {...props}
        />
        {hasValue && onClear ? (
          <button
            type="button"
            aria-label={clearLabel ?? t("search.clear")}
            disabled={disabled || readOnly}
            onClick={onClear}
            className={styles.clear()}
          >
            <Glyph name="x" className={styles.clearIcon()} />
          </button>
        ) : null}
      </div>
    );
  },
);

SearchInput.displayName = "SearchInput";
