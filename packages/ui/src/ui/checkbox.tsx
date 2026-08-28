import * as React from "react";
import {
  Checkbox as BaseCheckbox,
  type CheckboxRootProps as BaseCheckboxRootProps,
} from "@base-ui/react/checkbox";
import { Glyph } from "../chrome/Glyph";
import { tv, type VariantProps } from "../lib/variants";
import { interactiveSurface } from "./widget-control";

export const checkboxVariants = tv({
  slots: {
    // Checkbox is a check square (not a box-surface widget control), so it
    // composes only the L0 interactive fragment (focus ring + disabled dimming)
    // and keeps its own border/fill and invalid treatment.
    root: `inline-flex shrink-0 cursor-pointer items-center justify-center rounded-6 border border-border-strong bg-sheet text-on-brand hover:border-brand data-[checked]:border-brand data-[checked]:bg-brand data-[indeterminate]:border-brand data-[indeterminate]:bg-brand ${interactiveSurface(
      { focus: "visible", disabled: "data" },
    )}`,
    indicator: "flex size-full items-center justify-center text-on-brand [&_svg]:size-3",
  },
  variants: {
    size: {
      sm: { root: "size-3.5" },
      md: { root: "size-4" },
      lg: { root: "size-5 rounded-6", indicator: "[&_svg]:size-3.5" },
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

export interface CheckboxVisualProps
  extends Pick<CheckboxRecipeProps, "invalid" | "size"> {
  checked?: boolean;
  className?: string;
  indeterminate?: boolean;
}

export function CheckboxVisual({
  checked = false,
  className,
  indeterminate = false,
  invalid = false,
  size = "md",
}: CheckboxVisualProps): React.ReactElement {
  const styles = checkboxVariants({ size, invalid });
  return (
    <span
      aria-hidden="true"
      className={styles.root({ className })}
      data-checked={checked ? "" : undefined}
      data-indeterminate={indeterminate ? "" : undefined}
    >
      {checked || indeterminate ? (
        <span className={styles.indicator()}>
          <CheckboxIndicatorIcon indeterminate={indeterminate} />
        </span>
      ) : null}
    </span>
  );
}

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
              <CheckboxIndicatorIcon indeterminate={state.indeterminate} />
            </span>
          )}
        />
        {children}
      </BaseCheckbox.Root>
    );
  },
);

Checkbox.displayName = "Checkbox";

function CheckboxIndicatorIcon({
  indeterminate,
}: {
  indeterminate: boolean;
}): React.ReactElement {
  return <Glyph name={indeterminate ? "minus" : "check"} strokeWidth={3} />;
}

function checkboxChangeEvent(checked: boolean): React.ChangeEvent<HTMLInputElement> {
  return {
    currentTarget: { checked },
    target: { checked },
  } as React.ChangeEvent<HTMLInputElement>;
}
