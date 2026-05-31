import * as React from "react";
import {
  Checkbox as BaseCheckbox,
  type CheckboxRootProps as BaseCheckboxRootProps,
} from "@base-ui-components/react/checkbox";
import { Check, Minus } from "lucide-react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

export const checkboxVariants = tv({
  slots: {
    root: "inline-flex shrink-0 cursor-pointer items-center justify-center rounded border border-border-strong bg-sheet text-on-brand outline-none transition-colors hover:border-brand focus-visible:focus-ring data-[checked]:border-brand data-[checked]:bg-brand data-[indeterminate]:border-brand data-[indeterminate]:bg-brand data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60",
    indicator: "flex size-full items-center justify-center text-on-brand [&_svg]:size-3",
  },
  variants: {
    size: {
      sm: { root: "size-3.5" },
      md: { root: "size-4" },
      lg: { root: "size-5 rounded-md", indicator: "[&_svg]:size-3.5" },
    },
    invalid: {
      true: {
        root: "border-danger focus-visible:focus-ring-danger",
      },
      false: "",
    },
  },
  defaultVariants: {
    size: "md",
    invalid: false,
  },
});

type CheckboxRecipeProps = VariantProps<typeof checkboxVariants>;
type CheckedChangeHandler = NonNullable<BaseCheckboxRootProps["onCheckedChange"]>;
type CheckedChangeDetails = Parameters<CheckedChangeHandler>[1];

export type CheckboxSize = NonNullable<CheckboxRecipeProps["size"]>;

export type CheckboxProps = Omit<
  BaseCheckboxRootProps,
  "className" | "children" | "onCheckedChange"
> &
  CheckboxRecipeProps & {
    children?: React.ReactNode;
    className?: string;
    onChange?: React.ChangeEventHandler<HTMLInputElement>;
    onCheckedChange?: BaseCheckboxRootProps["onCheckedChange"];
  };

export const Checkbox = React.forwardRef<HTMLElement, CheckboxProps>(
  function Checkbox(
    {
      size = "md",
      invalid = false,
      indeterminate = false,
      className,
      children,
      onChange,
      onCheckedChange,
      ...props
    },
    ref,
  ) {
    const styles = checkboxVariants({ size, invalid });

    function handleCheckedChange(
      checked: boolean,
      eventDetails: CheckedChangeDetails,
    ) {
      onCheckedChange?.(checked, eventDetails);
      onChange?.(checkboxChangeEvent(checked));
    }

    return (
      <BaseCheckbox.Root
        ref={ref}
        className={styles.root({ className })}
        data-indeterminate={indeterminate ? "" : undefined}
        indeterminate={indeterminate}
        onCheckedChange={handleCheckedChange}
        {...props}
      >
        <BaseCheckbox.Indicator
          className={styles.indicator()}
          render={(indicatorProps, state) => (
            <span {...indicatorProps}>
              {state.indeterminate ? (
                <Minus aria-hidden="true" strokeWidth={3} />
              ) : (
                <Check aria-hidden="true" strokeWidth={3} />
              )}
            </span>
          )}
        />
        {children}
      </BaseCheckbox.Root>
    );
  },
);

Checkbox.displayName = "Checkbox";

function checkboxChangeEvent(checked: boolean): React.ChangeEvent<HTMLInputElement> {
  return {
    currentTarget: { checked },
    target: { checked },
  } as React.ChangeEvent<HTMLInputElement>;
}
