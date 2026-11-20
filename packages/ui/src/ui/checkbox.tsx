import * as React from "react";
import {
  Checkbox as BaseCheckbox,
  type CheckboxRootProps as BaseCheckboxRootProps,
} from "@base-ui/react/checkbox";
import { Glyph } from "../chrome/Glyph";
import { shownContent } from "../lib/shown-content";
import { tv, type VariantProps } from "../lib/variants";
import { Tooltip } from "./tooltip";
import { interactiveSurface, widgetControlPresentationProps, type WidgetControlSurfaceProps } from "./widget-control";

/**
 * @kind token
 */
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
      sm: { root: "size-3 rounded-2", indicator: "[&_svg]:size-2.5" },
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

export interface CheckboxVisualProps
  extends Pick<CheckboxRecipeProps, "invalid" | "size"> {
  checked?: boolean;
  className?: string;
  indeterminate?: boolean;
}

/**
 * @owner Checkbox
 */
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
  CheckboxRecipeProps & Pick<WidgetControlSurfaceProps, "presentation"> & {
    children?: React.ReactNode;
    className?: string;
    onChange?: React.ChangeEventHandler<HTMLInputElement>;
    onCheckedChange?: BaseCheckboxRootProps["onCheckedChange"];
    /**
     * Why the box cannot change; a reason disables it (null, false and "" are no
     * reason), as `Button.disabledReason` does a verb. The reason is the box's tooltip
     * and its accessible description, and the box stays focusable so keyboard users
     * reach it; a click, Space or Enter changes nothing, and the box swallows Space so
     * it does not scroll the page. A box given this prop, even with no reason, keeps
     * one element tree as its reason comes and goes, so a focused box keeps its focus.
     */
    disabledReason?: React.ReactNode;
  };

/**
 * @kind primitive
 */
export const Checkbox = React.forwardRef<HTMLElement, CheckboxProps>(
  function Checkbox(props, ref) {
    const {
      size = "md",
      presentation,
      invalid: invalidProp = false,
      indeterminate = false,
      className,
      children,
      onChange,
      onCheckedChange,
      disabled,
      disabledReason,
      "aria-describedby": describedBy,
      ...rest
    } = props;
    const invalid = invalidProp || rest["aria-invalid"] === true || rest["aria-invalid"] === "true";
    const styles = checkboxVariants({ size, ...widgetControlPresentationProps(presentation), invalid });
    const reasonId = React.useId();
    // A box given the prop keeps one element tree while its reason comes and goes.
    const reasonGiven = "disabledReason" in props;
    const reason = shownContent(disabledReason);

    function handleCheckedChange(
      checked: boolean,
      eventDetails: CheckedChangeDetails,
    ) {
      onCheckedChange?.(checked, eventDetails);
      onChange?.(checkboxChangeEvent(checked));
    }

    // Base UI skips a disabled box's key handlers, so a box a reason keeps focusable
    // swallows Space itself, which would scroll the page; its capture handler still runs.
    const swallowSpace: CheckboxProps["onKeyDownCapture"] = (event) => {
      rest.onKeyDownCapture?.(event);
      if (event.key === " ") event.preventDefault();
    };

    const box = (
      <BaseCheckbox.Root
        ref={ref}
        className={styles.root({ className })}
        data-indeterminate={indeterminate ? "" : undefined}
        indeterminate={indeterminate}
        onCheckedChange={handleCheckedChange}
        aria-describedby={reason === null ? describedBy : [describedBy, reasonId].filter(Boolean).join(" ")}
        {...rest}
        // Base UI ignores a disabled box's activation and marks it `aria-disabled`; a
        // reason keeps it in the tab order, where Base UI would take it out.
        disabled={disabled || reason !== null}
        {...(reason === null ? {} : { tabIndex: rest.tabIndex ?? 0, onKeyDownCapture: swallowSpace })}
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
    if (!reasonGiven) return box;
    return (
      <>
        <Tooltip label={reason} disabled={reason === null}>{box}</Tooltip>
        <span id={reasonId} hidden>{reason}</span>
      </>
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
