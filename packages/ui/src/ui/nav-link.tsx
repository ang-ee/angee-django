import * as React from "react";

import { useInAppLink } from "../lib/in-app-link";
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

    const link = useInAppLink(href, { ...props, onClick });

    function handleClick(event: React.MouseEvent<HTMLElement>): void {
      if (disabled) {
        event.preventDefault();
        return;
      }
      link.onClick(event);
    }

    const renderProps: Record<string, unknown> = {
      ...props,
      ...link,
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
