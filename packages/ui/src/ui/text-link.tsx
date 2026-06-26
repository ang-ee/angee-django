import * as React from "react";

import { useRender, type UseRenderRenderProp } from "../lib/slot";
import { tv, type VariantProps } from "../lib/variants";

export const textLinkVariants = tv({
  base: "outline-none transition-colors focus-visible:focus-ring",
  variants: {
    variant: {
      default:
        "font-medium text-link underline-offset-4 hover:text-brand hover:underline",
      muted: "text-fg-muted underline-offset-4 hover:text-fg hover:no-underline",
      "block-card":
        "block rounded-md border border-border-subtle bg-sheet p-3 text-fg shadow-xs hover:border-border hover:bg-inset hover:no-underline",
    },
    disabled: {
      true: "pointer-events-none cursor-not-allowed opacity-60",
      false: "",
    },
  },
  defaultVariants: {
    variant: "default",
    disabled: false,
  },
});

export type TextLinkRecipeProps = VariantProps<typeof textLinkVariants>;

export type TextLinkVariant = NonNullable<TextLinkRecipeProps["variant"]>;

type TextLinkState = {
  disabled: boolean;
  external: boolean;
};

export type TextLinkProps = Omit<
  React.AnchorHTMLAttributes<HTMLAnchorElement>,
  "className" | "color"
> &
  TextLinkRecipeProps & {
    asChild?: boolean;
    className?: string;
    disabled?: boolean;
    onNavigate?: (href: string) => void;
    render?: UseRenderRenderProp<TextLinkState>;
  };

export const TextLink = React.forwardRef<HTMLElement, TextLinkProps>(
  function TextLink(
    {
      asChild = false,
      children,
      className,
      disabled = false,
      href,
      onClick,
      onNavigate,
      rel,
      render,
      target,
      variant = "default",
      ...props
    },
    ref,
  ) {
    const child = asChild
      ? (React.Children.only(children) as React.ReactElement<Record<string, unknown>>)
      : undefined;
    const external = target === "_blank";

    function handleClick(event: React.MouseEvent<HTMLElement>): void {
      if (disabled) {
        event.preventDefault();
        return;
      }
      onClick?.(event as React.MouseEvent<HTMLAnchorElement>);
      handleClientNavigation(event, href, onNavigate);
    }

    return useRender<TextLinkState, HTMLElement>({
      defaultTagName: "a",
      ref,
      render: asChild ? child : render,
      state: {
        disabled,
        external,
      },
      props: {
        ...props,
        "aria-disabled": disabled ? true : props["aria-disabled"],
        children: asChild ? undefined : children,
        className: textLinkVariants({ className, disabled, variant }),
        href,
        onClick: handleClick,
        rel: external ? (rel ?? "noopener noreferrer") : rel,
        tabIndex: disabled ? -1 : props.tabIndex,
        target,
      },
    });
  },
);
TextLink.displayName = "TextLink";

function handleClientNavigation(
  event: React.MouseEvent<HTMLElement>,
  href: string | undefined,
  onNavigate: ((href: string) => void) | undefined,
): void {
  if (!href || !onNavigate || href.startsWith("#")) return;
  if (event.defaultPrevented || event.button !== 0) return;
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;

  const target = event.currentTarget.getAttribute("target");
  if (target && target !== "_self") return;

  event.preventDefault();
  onNavigate(href);
}
