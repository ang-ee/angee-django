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

/** @deprecated Use `NavLinkVariant` and translate `default` to `inline`. */
export type TextLinkVariant = "default" | "muted" | "block-card";

const TEXT_LINK_VARIANTS = {
  default: "inline",
  muted: "muted",
  "block-card": "block-card",
} as const satisfies Record<TextLinkVariant, NavLinkVariant>;

/** @deprecated Use `NavLinkRecipeProps`. */
export type TextLinkRecipeProps = Pick<NavLinkRecipeProps, "disabled"> & {
  variant?: TextLinkVariant;
};

type TextLinkVariantOptions = Omit<
  NonNullable<Parameters<typeof navLinkVariants>[0]>,
  "active" | "affordance" | "variant"
> & {
  variant?: TextLinkVariant;
};

/** @deprecated Use `navLinkVariants` with the translated variant. */
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

/**
 * @deprecated Use `NavLinkProps` with the translated variant. Keeps TextLink's
 * historical contract: `href` is optional on its own (NavLink requires a
 * destination through `href`, `asChild`, or `render`).
 */
export type TextLinkProps = Omit<
  Extract<NavLinkProps, { href: string }>,
  "active" | "affordance" | "href" | "to" | "variant"
> & {
  href?: string;
  variant?: TextLinkVariant;
};

/** @deprecated Use `NavLink` with the translated variant. */
export const TextLink = React.forwardRef<HTMLElement, TextLinkProps>(
  function TextLink({ variant = "default", ...props }, ref) {
    // The historical contract allowed a missing href; the canonical owner's
    // destination union is satisfied by the compatibility cast only here.
    return <NavLink {...(props as NavLinkProps)} ref={ref} variant={TEXT_LINK_VARIANTS[variant]} />;
  },
);
TextLink.displayName = "TextLink";
