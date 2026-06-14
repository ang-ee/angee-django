import * as React from "react";
import { Avatar as BaseAvatar } from "@base-ui/react/avatar";
import type {
  AvatarFallbackProps as BaseAvatarFallbackProps,
  AvatarImageProps as BaseAvatarImageProps,
  AvatarRootProps as BaseAvatarRootProps,
} from "@base-ui/react/avatar";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

export const avatarVariants = tv({
  slots: {
    root: "inline-flex shrink-0 overflow-hidden rounded-full bg-avatar-default-bg font-semibold text-on-brand",
    image: "size-full object-cover",
    fallback: "flex size-full items-center justify-center",
  },
  variants: {
    size: {
      sm: { root: "size-avatar-sm text-2xs" },
      md: { root: "size-avatar-md text-2xs" },
      lg: { root: "size-avatar-lg text-xs" },
      xl: { root: "size-avatar-xl text-base" },
      xxl: { root: "size-avatar-xxl text-22 font-bold" },
    },
  },
  defaultVariants: {
    size: "md",
  },
});

export const avatarStackVariants = tv({
  base: "inline-flex [&>*+*]:-ml-1.5 [&>*+*]:ring-2 [&>*+*]:ring-sheet",
});

type AvatarRecipeProps = VariantProps<typeof avatarVariants>;

export type AvatarSize = NonNullable<AvatarRecipeProps["size"]>;
export type AvatarColorSlot = 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8;

export type AvatarImageProps = Omit<BaseAvatarImageProps, "className"> & {
  className?: string;
};

export const AvatarImage = React.forwardRef<HTMLImageElement, AvatarImageProps>(
  function AvatarImage({ className, ...props }, ref) {
    const styles = avatarVariants();
    return (
      <BaseAvatar.Image
        ref={ref}
        className={styles.image({ className })}
        {...props}
      />
    );
  },
);
AvatarImage.displayName = "AvatarImage";

export type AvatarFallbackProps = Omit<
  BaseAvatarFallbackProps,
  "className"
> & {
  className?: string;
};

export const AvatarFallback = React.forwardRef<
  HTMLSpanElement,
  AvatarFallbackProps
>(function AvatarFallback({ className, ...props }, ref) {
  const styles = avatarVariants();
  return (
    <BaseAvatar.Fallback
      ref={ref}
      className={styles.fallback({ className })}
      {...props}
    />
  );
});
AvatarFallback.displayName = "AvatarFallback";

export type AvatarProps = Omit<
  BaseAvatarRootProps,
  "children" | "className" | "color"
> &
  AvatarRecipeProps & {
    alt?: string;
    children?: React.ReactNode;
    className?: string;
    fallbackClassName?: string;
    fallbackDelay?: number;
    imageClassName?: string;
    initials?: string;
    src?: string;
  };

export function hashAvatarColor(seed: string): AvatarColorSlot {
  let hash = 0;
  for (const char of seed) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return ((hash % 8) + 1) as AvatarColorSlot;
}

export const Avatar = React.forwardRef<HTMLSpanElement, AvatarProps>(
  function Avatar(
    {
      alt,
      children,
      className,
      fallbackClassName,
      fallbackDelay,
      imageClassName,
      initials,
      size = "md",
      src,
      style,
      ...props
    },
    ref,
  ) {
    const styles = avatarVariants({ size });
    const slot = initials ? hashAvatarColor(initials) : undefined;
    const background = slot ? { background: `var(--avatar-grad-${slot})` } : {};

    return (
      <BaseAvatar.Root
        ref={ref}
        className={styles.root({ className })}
        style={{ ...background, ...style }}
        {...props}
      >
        {src ? (
          <AvatarImage
            alt={alt ?? initials ?? ""}
            className={imageClassName}
            src={src}
          />
        ) : null}
        <AvatarFallback className={fallbackClassName} delay={fallbackDelay}>
          {children ?? initials}
        </AvatarFallback>
      </BaseAvatar.Root>
    );
  },
);
Avatar.displayName = "Avatar";

export type AvatarStackProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> & {
  className?: string;
};

export const AvatarStack = React.forwardRef<HTMLDivElement, AvatarStackProps>(
  function AvatarStack({ className, ...props }, ref) {
    return (
      <div
        ref={ref}
        className={cn(avatarStackVariants(), className)}
        {...props}
      />
    );
  },
);
AvatarStack.displayName = "AvatarStack";
