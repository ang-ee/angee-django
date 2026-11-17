import * as React from "react";

import { cn } from "../lib/cn";
import { useRender, type UseRenderRenderProp } from "../lib/slot";
import { tv, type VariantProps } from "../lib/variants";
import { Checkbox } from "./checkbox";
import { textRoleVariants } from "./text";
import { interactiveSurface } from "./widget-control";

/**
 * @kind token
 */
export const cardVariants = tv({
  base: "rounded-8 border bg-sheet text-fg shadow-xs transition-colors",
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
      true: cn(interactiveSurface({ focus: "visible", disabled: "pseudo" }),
        "w-full cursor-pointer text-left hover:border-border-strong hover:shadow-sm"),
      false: "",
    },
    selected: {
      true: "selected border-brand hover:selected hover:border-brand",
      false: "",
    },
  },
  defaultVariants: {
    variant: "default",
    density: "md",
    interactive: false,
    selected: false,
  },
});

/**
 * @kind token
 */
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

/**
 * @kind token
 */
export const cardTitleVariants = tv({
  // The shared title role (display face, weight, color); the density sets the size
  // and its line height (a line-height class here would be merged away by it).
  base: textRoleVariants({ role: "title" }),
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

/**
 * @kind token
 */
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

/**
 * @kind token
 */
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

/**
 * @kind token
 */
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

/**
 * A collection card's frame (a gallery or board card) on the `cardVariants`
 * surface. `inset` pads the content; `flush` lets the body reach the card's
 * edges, so an art header bleeds, and overlays the checkbox (top left) and the
 * drag handle (top right). An inset card keeps a control column only for a drag
 * handle, the checkbox joining it there; without one the checkbox overlays the
 * top right corner, so an invisible checkbox reserves no space. Controls show on
 * hover and focus, while selected, and always on a coarse pointer. The focus ring
 * is the frame's, drawn when its body (`data-slot="card-body"`) has keyboard
 * focus, so an edge-to-edge body never clips it. Hover, selection and drag states
 * live here so a selected card keeps its brand border on hover.
 * @kind token
 */
export const cardFrameVariants = tv({
  slots: {
    root: "group/card relative min-w-0 overflow-hidden outline-none has-[[data-slot=card-body]:focus-visible]:focus-ring",
    row: "min-w-0 max-w-full",
    body: "min-w-0 outline-none",
    controls: "contents",
    selection:
      "opacity-0 transition-opacity group-hover/card:opacity-100 group-focus-within/card:opacity-100 data-[selected=true]:opacity-100 pointer-coarse:opacity-100",
    handle: "",
    footer: cn(cardFooterVariants({ density: "sm" }), "justify-end border-t border-border-subtle"),
  },
  variants: {
    frame: {
      inset: {
        row: "flex items-start gap-2 p-3",
        body: "flex-1",
      },
      flush: {
        selection: "absolute left-2 top-2 z-10",
        handle:
          "absolute right-2 top-2 z-10 rounded-6 bg-sheet/90 opacity-0 shadow-xs transition-opacity group-hover/card:opacity-100 group-focus-within/card:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100",
      },
    },
    /** The card carries a drag handle. */
    handle: { true: "", false: "" },
    /** The card's body is a link or button. */
    interactive: { true: { root: "cursor-pointer hover:shadow-md motion-safe:hover:-translate-y-0.5" }, false: "" },
    selected: { true: "", false: "" },
    dragging: {
      true: { root: "z-10 border-border-focus shadow-lg transition-none" },
      false: { root: "transition" },
    },
  },
  compoundVariants: [
    { frame: "inset", handle: true, class: { controls: "flex shrink-0 flex-col items-center gap-1" } },
    { frame: "inset", handle: false, class: { selection: "absolute right-2 top-2 z-10" } },
    // Only an unselected card strengthens its border on hover; a selected one keeps the brand border.
    { interactive: true, selected: false, dragging: false, class: { root: "hover:border-border-strong" } },
  ],
  defaultVariants: {
    frame: "inset",
    handle: false,
    interactive: false,
    selected: false,
    dragging: false,
  },
});

/** How a collection card's content meets its frame. */
export type CardFrame = NonNullable<VariantProps<typeof cardFrameVariants>["frame"]>;

export interface CardSelectionProps {
  selected: boolean;
  onSelectedChange: (selected: boolean) => void;
  /** The checkbox's accessible name, e.g. "Select <record>". */
  label: string;
  className?: string;
}

/**
 * A collection card's selection checkbox. It is a sibling of the card's link or
 * button, so toggling it never opens the record; it shows on hover and focus,
 * and stays while the card is selected. The frame places it (`cardFrameVariants`).
 * @owner Card
 */
export function CardSelection({ selected, onSelectedChange, label, className }: CardSelectionProps): React.ReactElement {
  return (
    <div data-slot="card-selection" data-selected={selected || undefined} className={className}>
      <Checkbox checked={selected} onCheckedChange={(next) => onSelectedChange(next)} aria-label={label} />
    </div>
  );
}

type CardRecipeProps = VariantProps<typeof cardVariants>;
type CardState = {
  interactive: boolean;
  placeholder: boolean;
  selected: boolean;
};

export type CardDensity = NonNullable<CardRecipeProps["density"]>;

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

/**
 * @kind token
 */
export const CARD_BASE = cardVariants({
  density: "md",
  interactive: false,
  variant: "default",
});

/**
 * @kind primitive
 */
export const Card = React.forwardRef<HTMLElement, CardProps>(function Card(
  {
    asChild = false,
    children,
    className,
    density = "md",
    interactive = false,
    placeholder = false,
    render,
    selected = false,
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
        selected,
        variant: resolvedVariant,
      }),
      className,
    ),
    children: asChild ? undefined : children,
  };

  if (type !== undefined || (interactive && !asChild)) {
    renderProps.type = interactive && !asChild ? (type ?? "button") : type;
  }

  return useRender<CardState, HTMLElement>({
    defaultTagName: interactive ? "button" : "article",
    ref,
    render: asChild ? child : render,
    state: {
      interactive,
      selected,
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
/**
 * @owner Card
 */
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
/**
 * @owner Card
 */
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
/**
 * @owner Card
 */
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
/**
 * @owner Card
 */
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
/**
 * @owner Card
 */
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
