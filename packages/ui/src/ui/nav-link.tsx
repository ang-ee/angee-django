import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useInAppLink } from "../lib/in-app-link";
import { useRender, type UseRenderRenderProp } from "../lib/slot";
import { tv, type VariantProps } from "../lib/variants";
import { cardVariants } from "./card";

export type NavLinkAffordance = "none" | "forward" | "outward";

export const navLinkVariants = tv({
  base: "outline-none transition-colors focus-visible:focus-ring",
  variants: {
    variant: {
      unstyled: "",
      inline:
        "font-medium text-link underline-offset-4 hover:text-brand hover:underline",
      muted: "text-fg-muted underline-offset-4 hover:text-fg hover:no-underline",
      block: "block",
      "block-card": [
        cardVariants.base,
        cardVariants.variants.variant.default,
        cardVariants.variants.interactive.true,
        "block p-3 text-fg hover:no-underline",
      ],
    },
    active: {
      true: "",
      false: "",
    },
    disabled: {
      true: "pointer-events-none cursor-not-allowed opacity-60",
      false: "",
    },
    affordance: {
      none: "",
      forward: "inline-flex items-center gap-1",
      outward: "inline-flex items-center gap-1",
    },
  },
  defaultVariants: {
    variant: "unstyled",
    active: false,
    disabled: false,
    affordance: "none",
  },
});

export type NavLinkRecipeProps = VariantProps<typeof navLinkVariants>;

export type NavLinkVariant = NonNullable<NavLinkRecipeProps["variant"]>;

export type NavLinkState = {
  active: boolean;
  disabled: boolean;
  external: boolean;
};

export type NavLinkProps = Omit<
  React.AnchorHTMLAttributes<HTMLAnchorElement>,
  "className" | "color"
> &
  NavLinkRecipeProps & {
    active?: boolean;
    affordance?: NavLinkAffordance;
    asChild?: boolean;
    className?: string;
    disabled?: boolean;
    render?: UseRenderRenderProp<NavLinkState>;
    to?: string;
  };

export const NavLink = React.forwardRef<HTMLElement, NavLinkProps>(
  function NavLink(
    {
      active = false,
      affordance = "none",
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
    const affordanceGlyph = affordance === "forward" ? (
      <Glyph decorative name="arrow-right" size="1em" />
    ) : affordance === "outward" ? (
      <Glyph decorative name="arrow-up-right" size="1em" />
    ) : null;
    const renderedChild = child && affordanceGlyph
      ? React.cloneElement(child, undefined, child.props.children as React.ReactNode, affordanceGlyph)
      : child;

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
      children: asChild ? undefined : (
        <>
          {children}
          {affordanceGlyph}
        </>
      ),
      className: navLinkVariants({ active, affordance, className, disabled, variant }),
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
      render: asChild ? renderedChild : render,
      state: {
        active,
        disabled,
        external,
      },
      props: renderProps,
    });
  },
);
NavLink.displayName = "NavLink";
