import * as React from "react";
import {
  Button as BaseButton,
  type ButtonProps as BaseUIButtonProps,
} from "@base-ui/react/button";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { CountBadge } from "./count-badge";
import { Spinner, type SpinnerSize } from "./spinner";
import { interactiveSurfaceVariants } from "./widget-control";

/**
 * @kind token
 */
export const buttonVariants = tv({
  extend: interactiveSurfaceVariants,
  base: "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-6 border font-medium leading-none [&_.glyph]:size-3.5",
  variants: {
    variant: {
      primary:
        "border-brand bg-brand text-on-brand hover:border-brand-hover hover:bg-brand-hover active:border-brand-active active:bg-brand-active",
      secondary:
        "border-border-strong bg-inset text-fg hover:border-border-strong hover:bg-sheet",
      ghost:
        "border-transparent bg-transparent text-fg-2 hover:bg-inset hover:text-fg",
      danger:
        "border-danger bg-danger text-on-danger hover:border-danger-hover hover:bg-danger-hover active:border-danger-active active:bg-danger-active",
      link: "border-transparent bg-transparent text-link hover:text-brand",
    },
    size: {
      sm: "h-btn-sm px-2 text-12",
      md: "h-btn-md px-3 text-13",
      lg: "h-btn-lg px-4 text-sm",
      iconSm: "size-icon-btn-sm px-0 text-12 [&_.glyph]:size-3.5",
      iconMd: "size-icon-btn-md px-0 text-13 [&_.glyph]:size-4",
      iconLg: "size-icon-btn-lg px-0 text-sm [&_.glyph]:size-4",
    },
    // The one selected surface (`selected` in styles/index.css); hover keeps it.
    active: {
      true: "selected hover:selected",
      false: "",
    },
    loading: {
      true: "cursor-wait",
      false: "",
    },
  },
  defaultVariants: {
    variant: "secondary",
    size: "md",
    active: false,
    loading: false,
    focus: "visible",
    disabled: "pseudo",
  },
});

type ButtonRecipeProps = VariantProps<typeof buttonVariants>;
type BaseButtonInteropProps = Pick<
  BaseUIButtonProps,
  "focusableWhenDisabled" | "nativeButton" | "render"
>;

export type ButtonVariant = NonNullable<ButtonRecipeProps["variant"]>;
export type ButtonSize = NonNullable<ButtonRecipeProps["size"]>;

function spinnerSizeForButton(size: ButtonSize): SpinnerSize {
  return size === "lg" || size === "iconLg" ? "md" : "sm";
}

export type ButtonProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "className" | "color"
> &
  BaseButtonInteropProps &
  Pick<ButtonRecipeProps, "variant" | "size" | "active" | "loading"> & {
    asChild?: boolean;
    className?: string;
    loading?: boolean;
    loadingText?: React.ReactNode;
    pending?: boolean;
    /** A toggle button's state: sets `aria-pressed` and the selected treatment. */
    pressed?: boolean;
    /** A count riding on the button's corner (needs you, unread); nothing when 0. */
    badge?: number;
  };

/**
 * @kind primitive
 */
export const Button = React.forwardRef<HTMLElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    active = false,
    loading = false,
    loadingText,
    pending = false,
    pressed,
    badge,
    asChild = false,
    className,
    children,
    disabled,
    nativeButton,
    render,
    ...props
  },
  ref,
) {
  const child = asChild
    ? (React.Children.only(children) as React.ReactElement<Record<string, unknown>>)
    : undefined;
  const isLoading = loading || pending;
  const buttonContent =
    isLoading && loadingText !== undefined ? loadingText : children;

  return (
    <BaseButton
      ref={ref}
      className={cn(
        buttonVariants({ variant, size, active: active || pressed === true, loading: isLoading }),
        badge ? "relative" : undefined,
        className,
      )}
      data-loading={isLoading ? "" : undefined}
      data-pending={pending ? "" : undefined}
      aria-pressed={pressed}
      aria-busy={isLoading || undefined}
      disabled={disabled || isLoading}
      nativeButton={asChild ? (nativeButton ?? false) : nativeButton}
      render={asChild ? child : render}
      {...props}
    >
      {asChild ? undefined : (
        <>
          {isLoading ? (
            <Spinner
              aria-hidden
              size={spinnerSizeForButton(size)}
              tone={variant === "primary" || variant === "danger" ? "inverse" : "current"}
            />
          ) : null}
          {buttonContent}
          {badge ? (
            <CountBadge value={badge} tone="warning" variant="solid" size="xs" placement="overlay" />
          ) : null}
        </>
      )}
    </BaseButton>
  );
});

Button.displayName = "Button";
