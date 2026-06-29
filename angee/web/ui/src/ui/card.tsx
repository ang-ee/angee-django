import * as React from "react";

import { cn } from "../lib/cn";
import { useRender, type UseRenderRenderProp } from "../lib/slot";
import { tv, type VariantProps } from "../lib/variants";
import { textRoleVariants } from "./text";

export const cardVariants = tv({
  base: "rounded-6 border bg-sheet text-fg shadow-xs transition-colors",
  variants: {
    variant: {
      default: "border-border-subtle",
      elevated: "border-border shadow-sm",
      placeholder:
        "border-dashed border-border bg-inset text-fg-muted shadow-none",
    },
    density: {
      sm: "text-13",
      md: "text-13",
      lg: "text-sm",
    },
    interactive: {
      true:
        "w-full cursor-pointer text-left outline-none hover:border-border-strong hover:shadow-sm focus-visible:focus-ring",
      false: "",
    },
  },
  defaultVariants: {
    variant: "default",
    density: "md",
    interactive: false,
  },
});

export const cardHeaderVariants = tv({
  base: "flex flex-col gap-1.5",
  variants: {
    density: {
      sm: "px-3 pt-3",
      md: "px-4 pt-4",
      lg: "px-5 pt-5",
    },
  },
  defaultVariants: {
    density: "md",
  },
});

export const cardTitleVariants = tv({
  base: "text-sm font-semibold leading-tight text-fg",
  variants: {
    density: {
      sm: "text-13",
      md: "text-sm",
      lg: "text-base",
    },
  },
  defaultVariants: {
    density: "md",
  },
});

export const cardDescriptionVariants = tv({
  base: cn(textRoleVariants({ role: "meta" }), "leading-snug"),
  variants: {
    density: {
      sm: "text-2xs",
      md: "text-13",
      lg: "text-sm",
    },
  },
  defaultVariants: {
    density: "md",
  },
});

export const cardContentVariants = tv({
  base: "text-fg",
  variants: {
    density: {
      sm: "px-3 py-2",
      md: "px-4 py-3",
      lg: "px-5 py-4",
    },
  },
  defaultVariants: {
    density: "md",
  },
});

export const cardFooterVariants = tv({
  base: "flex items-center gap-2",
  variants: {
    density: {
      sm: "px-3 pb-3 pt-2",
      md: "px-4 pb-4 pt-2",
      lg: "px-5 pb-5 pt-3",
    },
  },
  defaultVariants: {
    density: "md",
  },
});

type CardRecipeProps = VariantProps<typeof cardVariants>;
type CardState = {
  interactive: boolean;
  placeholder: boolean;
};

export type CardDensity = NonNullable<CardRecipeProps["density"]>;
export type CardVariant = NonNullable<CardRecipeProps["variant"]>;

export type CardProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  "className" | "color"
> &
  CardRecipeProps & {
    asChild?: boolean;
    className?: string;
    placeholder?: boolean;
    render?: UseRenderRenderProp<CardState>;
  };

export const CARD_BASE = cardVariants({
  density: "md",
  interactive: false,
  variant: "default",
});
export const CARD_INTERACTIVE_BASE = cardVariants({
  density: "md",
  interactive: true,
  variant: "default",
});

export const Card = React.forwardRef<HTMLElement, CardProps>(function Card(
  {
    asChild = false,
    children,
    className,
    density = "md",
    interactive = false,
    placeholder = false,
    render,
    type,
    variant = "default",
    ...props
  },
  ref,
) {
  const resolvedVariant = placeholder ? "placeholder" : variant;
  const child = asChild
    ? (React.Children.only(children) as React.ReactElement<Record<string, unknown>>)
    : undefined;
  const renderProps: Record<string, unknown> = {
    ...props,
    className: cn(
      cardVariants({
        density,
        interactive,
        variant: resolvedVariant,
      }),
      className,
    ),
    children: asChild ? undefined : children,
  };

  if (interactive || type !== undefined) {
    renderProps.type = interactive ? (type ?? "button") : type;
  }

  return useRender<CardState, HTMLElement>({
    defaultTagName: interactive ? "button" : "article",
    ref,
    render: asChild ? child : render,
    state: {
      interactive,
      placeholder: resolvedVariant === "placeholder",
    },
    props: renderProps,
  });
});

Card.displayName = "Card";

type CardPartProps<TElement extends HTMLElement> = Omit<
  React.HTMLAttributes<TElement>,
  "className" | "color"
> & {
  className?: string;
  density?: CardDensity;
};

export type CardHeaderProps = CardPartProps<HTMLDivElement>;
export const CardHeader = React.forwardRef<HTMLDivElement, CardHeaderProps>(
  function CardHeader({ className, density = "md", ...props }, ref) {
    return (
      <div
        ref={ref}
        className={cn(cardHeaderVariants({ density }), className)}
        {...props}
      />
    );
  },
);
CardHeader.displayName = "CardHeader";

export type CardTitleProps = CardPartProps<HTMLHeadingElement>;
export const CardTitle = React.forwardRef<HTMLHeadingElement, CardTitleProps>(
  function CardTitle({ className, density = "md", ...props }, ref) {
    return (
      <h3
        ref={ref}
        className={cn(cardTitleVariants({ density }), className)}
        {...props}
      />
    );
  },
);
CardTitle.displayName = "CardTitle";

export type CardDescriptionProps = CardPartProps<HTMLParagraphElement>;
export const CardDescription = React.forwardRef<
  HTMLParagraphElement,
  CardDescriptionProps
>(function CardDescription({ className, density = "md", ...props }, ref) {
  return (
    <p
      ref={ref}
      className={cn(cardDescriptionVariants({ density }), className)}
      {...props}
    />
  );
});
CardDescription.displayName = "CardDescription";

export type CardContentProps = CardPartProps<HTMLDivElement>;
export const CardContent = React.forwardRef<HTMLDivElement, CardContentProps>(
  function CardContent({ className, density = "md", ...props }, ref) {
    return (
      <div
        ref={ref}
        className={cn(cardContentVariants({ density }), className)}
        {...props}
      />
    );
  },
);
CardContent.displayName = "CardContent";

export type CardFooterProps = CardPartProps<HTMLDivElement>;
export const CardFooter = React.forwardRef<HTMLDivElement, CardFooterProps>(
  function CardFooter({ className, density = "md", ...props }, ref) {
    return (
      <div
        ref={ref}
        className={cn(cardFooterVariants({ density }), className)}
        {...props}
      />
    );
  },
);
CardFooter.displayName = "CardFooter";
