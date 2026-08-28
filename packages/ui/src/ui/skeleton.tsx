import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

export const skeletonVariants = tv({
  base:
    "relative overflow-hidden bg-inset text-transparent before:absolute before:inset-0 before:-translate-x-full before:bg-gradient-to-r before:from-transparent before:via-sheet before:to-transparent before:animate-skeleton-shimmer",
  variants: {
    shape: {
      block: "w-full rounded-6",
      text: "w-full rounded-full",
      avatar: "aspect-square rounded-full",
    },
    size: {
      sm: "h-3",
      md: "h-4",
      lg: "h-6",
    },
    animated: {
      true: "",
      false: "before:hidden",
    },
  },
  defaultVariants: {
    animated: true,
    shape: "block",
    size: "md",
  },
});

type SkeletonRecipeProps = VariantProps<typeof skeletonVariants>;

export type SkeletonShape = NonNullable<SkeletonRecipeProps["shape"]>;
export type SkeletonSize = NonNullable<SkeletonRecipeProps["size"]>;

export type SkeletonProps = React.HTMLAttributes<HTMLDivElement> &
  SkeletonRecipeProps & {
    decorative?: boolean;
  };

export const Skeleton = React.forwardRef<HTMLDivElement, SkeletonProps>(
  function Skeleton(
    {
      animated = true,
      className,
      decorative = true,
      shape = "block",
      size = "md",
      ...props
    },
    ref,
  ) {
    return (
      <div
        ref={ref}
        {...props}
        aria-hidden={decorative ? true : props["aria-hidden"]}
        className={skeletonVariants({ animated, className, shape, size })}
      />
    );
  },
);
Skeleton.displayName = "Skeleton";

const DEFAULT_LINE_WIDTHS = ["w-full", "w-11/12", "w-2/3"] as const;

export type SkeletonTextProps = Omit<SkeletonProps, "children" | "shape"> & {
  lineClassName?: string;
  lines?: number;
};

export const SkeletonText = React.forwardRef<
  HTMLDivElement,
  SkeletonTextProps
>(function SkeletonText(
  {
    className,
    lineClassName,
    lines = 3,
    size = "sm",
    ...props
  },
  ref,
) {
  const count = Math.max(1, Math.floor(lines));
  return (
    <div ref={ref} className={cn("space-y-2", className)}>
      {Array.from({ length: count }, (_, index) => (
        <Skeleton
          key={index}
          className={cn(
            DEFAULT_LINE_WIDTHS[index % DEFAULT_LINE_WIDTHS.length],
            lineClassName,
          )}
          shape="text"
          size={size}
          {...props}
        />
      ))}
    </div>
  );
});
SkeletonText.displayName = "SkeletonText";

export type SkeletonStatusProps = React.HTMLAttributes<HTMLDivElement> & {
  label: React.ReactNode;
};

export const SkeletonStatus = React.forwardRef<
  HTMLDivElement,
  SkeletonStatusProps
>(function SkeletonStatus(
  {
    children,
    className,
    label,
    ...props
  },
  ref,
) {
  return (
    <div
      ref={ref}
      {...props}
      aria-busy={props["aria-busy"] ?? true}
      aria-live={props["aria-live"] ?? "polite"}
      className={className}
      role={props.role ?? "status"}
    >
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
});
SkeletonStatus.displayName = "SkeletonStatus";
