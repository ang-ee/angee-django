import * as React from "react";

import { useInAppLink } from "../lib/in-app-link";
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
        "block rounded-6 border border-border-subtle bg-sheet p-3 text-fg shadow-xs hover:border-border hover:bg-inset hover:no-underline",
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

    const link = useInAppLink(href, { ...props, onClick });

    function handleClick(event: React.MouseEvent<HTMLElement>): void {
      if (disabled) {
        event.preventDefault();
        return;
      }
      link.onClick(event);
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
        ...link,
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
