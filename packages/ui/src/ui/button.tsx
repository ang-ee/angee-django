import * as React from "react";
import {
  Button as BaseButton,
  type ButtonProps as BaseUIButtonProps,
} from "@base-ui/react/button";

import { cn } from "../lib/cn";
import { shownContent } from "../lib/shown-content";
import { tv, type VariantProps } from "../lib/variants";
import { CountBadge } from "./count-badge";
import { Spinner, type SpinnerSize } from "./spinner";
import { Tooltip } from "./tooltip";
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
    // Base UI sets `data-disabled` on a disabled button, natively disabled or
    // focusable (`aria-disabled`), so both read as disabled.
    disabled: "data",
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
    /**
     * Why the verb cannot run; a reason disables it (null, false and "" are no
     * reason). The reason is the button's tooltip and its accessible description,
     * the button stays focusable so keyboard users reach it, and a link rendered
     * through it loses its `href`. A button given this prop, even with no reason,
     * keeps one element tree as its reason comes and goes, so a focused verb keeps
     * its focus.
     */
    disabledReason?: React.ReactNode;
  };

/** A link with a disabled reason opens nowhere: Base UI blocks its click and keys, not a middle-click or "open in new tab". */
function withoutHref<T>(element: T): T {
  return React.isValidElement<{ href?: unknown }>(element) ? React.cloneElement(element, { href: undefined }) as T : element;
}

/**
 * @kind primitive
 */
export const Button = React.forwardRef<HTMLElement, ButtonProps>(function Button(props, ref) {
  const {
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
    disabledReason,
    focusableWhenDisabled,
    nativeButton,
    render,
    "aria-describedby": describedBy,
    ...rest
  } = props;
  const reasonId = React.useId();
  // A button given the prop keeps one element tree while its reason comes and goes.
  const reasonGiven = "disabledReason" in props;
  const reason = shownContent(disabledReason);
  const child = asChild
    ? (React.Children.only(children) as React.ReactElement<Record<string, unknown>>)
    : undefined;
  const element = asChild ? child : render;
  const rendered: ButtonProps["render"] = reason === null ? element
    : typeof element === "function" ? (renderProps, state) => withoutHref(element(renderProps, state)) : withoutHref(element);
  const isLoading = loading || pending;
  const buttonContent =
    isLoading && loadingText !== undefined ? loadingText : children;

  const button = (
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
      aria-describedby={reason === null ? describedBy : [describedBy, reasonId].filter(Boolean).join(" ")}
      disabled={disabled || isLoading || reason !== null}
      focusableWhenDisabled={focusableWhenDisabled || reason !== null}
      nativeButton={asChild ? (nativeButton ?? false) : nativeButton}
      render={rendered}
      {...rest}
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
  if (!reasonGiven) return button;
  return (
    <>
      <Tooltip label={reason} disabled={reason === null} side="bottom">{button}</Tooltip>
      <span id={reasonId} hidden>{reason}</span>
    </>
  );
});

Button.displayName = "Button";
