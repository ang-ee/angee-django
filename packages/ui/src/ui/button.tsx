import * as React from "react";
import {
  Button as BaseButton,
  type ButtonProps as BaseUIButtonProps,
} from "@base-ui/react/button";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { Spinner, type SpinnerSize } from "./spinner";

export const buttonVariants = tv({
  base: "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border font-medium leading-none outline-none transition-colors focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60 [&_.glyph]:size-3.5",
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
      icon: "border-transparent bg-transparent text-fg-2 hover:bg-inset hover:text-fg",
    },
    size: {
      sm: "h-btn-sm px-2 text-xs",
      md: "h-btn-md px-3 text-13",
      lg: "h-btn-lg px-4 text-sm",
      iconSm: "size-icon-btn-sm rounded px-0 text-xs [&_.glyph]:size-3.5",
      iconMd: "size-icon-btn-md px-0 text-13 [&_.glyph]:size-4",
      iconLg: "size-icon-btn-lg rounded-lg px-0 text-sm [&_.glyph]:size-4",
    },
    active: {
      true:
        "bg-brand-soft text-brand-soft-text hover:bg-brand-soft hover:text-brand-soft-text",
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
  ButtonRecipeProps & {
    asChild?: boolean;
    className?: string;
    loading?: boolean;
    loadingText?: React.ReactNode;
    pending?: boolean;
  };

export const Button = React.forwardRef<HTMLElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    active = false,
    loading = false,
    loadingText,
    pending = false,
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
        buttonVariants({ variant, size, active, loading: isLoading }),
        className,
      )}
      data-loading={isLoading ? "" : undefined}
      data-pending={pending ? "" : undefined}
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
        </>
      )}
    </BaseButton>
  );
});

Button.displayName = "Button";
