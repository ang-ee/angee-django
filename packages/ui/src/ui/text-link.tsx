/**
 * @deprecated Compatibility surface for one release. Migrate `TextLink` to
 * `NavLink`, translating `default` to `inline`; `muted` and `block-card` keep
 * their names.
 */
import * as React from "react";

import {
  NavLink,
  navLinkVariants,
  type NavLinkProps,
  type NavLinkRecipeProps,
  type NavLinkVariant,
} from "./nav-link";

export type TextLinkVariant = "default" | "muted" | "block-card";

const TEXT_LINK_VARIANTS = {
  default: "inline",
  muted: "muted",
  "block-card": "block-card",
} as const satisfies Record<TextLinkVariant, NavLinkVariant>;

export type TextLinkRecipeProps = Pick<NavLinkRecipeProps, "disabled"> & {
  variant?: TextLinkVariant;
};

type TextLinkVariantOptions = Omit<
  NonNullable<Parameters<typeof navLinkVariants>[0]>,
  "active" | "affordance" | "variant"
> & {
  variant?: TextLinkVariant;
};

export function textLinkVariants({
  class: classValue,
  className,
  disabled,
  variant = "default",
}: TextLinkVariantOptions = {}): string {
  return navLinkVariants({
    className: className ?? classValue,
    disabled,
    variant: TEXT_LINK_VARIANTS[variant],
  });
}

export type TextLinkProps = Omit<
  NavLinkProps,
  "active" | "affordance" | "to" | "variant"
> & {
  variant?: TextLinkVariant;
};

/** @deprecated Use `NavLink` with the translated variant. */
export const TextLink = React.forwardRef<HTMLElement, TextLinkProps>(
  function TextLink({ variant = "default", ...props }, ref) {
    return <NavLink {...props} ref={ref} variant={TEXT_LINK_VARIANTS[variant]} />;
  },
);
TextLink.displayName = "TextLink";
