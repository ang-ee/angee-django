import * as React from "react";

import { useRender, type UseRenderRenderProp } from "../lib/slot";
import { tv, type VariantProps } from "../lib/variants";

export const navLinkVariants = tv({
  base: "outline-none transition-colors focus-visible:focus-ring",
  variants: {
    variant: {
      unstyled: "",
      inline:
        "font-medium text-link underline-offset-4 hover:text-brand hover:underline",
      block: "block",
    },
    active: {
      true: "",
      false: "",
    },
    disabled: {
      true: "pointer-events-none cursor-not-allowed opacity-60",
      false: "",
    },
  },
  defaultVariants: {
    variant: "unstyled",
    active: false,
    disabled: false,
  },
});

export type NavLinkRecipeProps = VariantProps<typeof navLinkVariants>;

export type NavLinkVariant = NonNullable<NavLinkRecipeProps["variant"]>;

type NavLinkState = {
  active: boolean;
  disabled: boolean;
};

export type NavLinkProps = Omit<
  React.AnchorHTMLAttributes<HTMLAnchorElement>,
  "className" | "color"
> &
  NavLinkRecipeProps & {
    active?: boolean;
    asChild?: boolean;
    className?: string;
    disabled?: boolean;
    onNavigate?: (href: string) => void;
    render?: UseRenderRenderProp<NavLinkState>;
    to?: string;
  } & {
    href: string;
  };

export const NavLink = React.forwardRef<HTMLElement, NavLinkProps>(
  function NavLink(
    {
      active = false,
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
      to,
      variant = "unstyled",
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

    const renderProps: Record<string, unknown> = {
      ...props,
      "aria-current": props["aria-current"] ?? (active ? "page" : undefined),
      "aria-disabled": disabled ? true : props["aria-disabled"],
      children: asChild ? undefined : children,
      className: navLinkVariants({ active, className, disabled, variant }),
      href,
      onClick: handleClick,
      rel: external ? (rel ?? "noopener noreferrer") : rel,
      tabIndex: disabled ? -1 : props.tabIndex,
      target,
    };

    if (to !== undefined) {
      renderProps.to = to;
    }

    return useRender<NavLinkState, HTMLElement>({
      defaultTagName: "a",
      ref,
      render: asChild ? child : render,
      state: {
        active,
        disabled,
      },
      props: renderProps,
    });
  },
);
NavLink.displayName = "NavLink";

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
