import * as React from "react";
import { Toolbar as BaseToolbar } from "@base-ui/react/toolbar";
import type {
  ToolbarButtonProps as BaseToolbarButtonProps,
  ToolbarGroupProps as BaseToolbarGroupProps,
  ToolbarRootProps as BaseToolbarRootProps,
  ToolbarSeparatorProps as BaseToolbarSeparatorProps,
} from "@base-ui/react/toolbar";

import { tv, type VariantProps } from "../lib/variants";
import { Button, type ButtonProps } from "./button";

/**
 * @kind token
 */
export const toolbarVariants = tv({
  slots: {
    root: "flex min-w-0 items-center gap-2",
    group: "flex min-w-0 items-center gap-2",
    separator: "shrink-0 bg-border-subtle data-[orientation=horizontal]:h-px data-[orientation=horizontal]:w-5 data-[orientation=vertical]:h-5 data-[orientation=vertical]:w-px",
    spacer: "min-w-0 flex-1",
  },
  variants: {
    surface: {
      chrome: {
        root:
          "h-control-h gap-4 border-b border-border-subtle bg-sheet px-4",
      },
      preview: {
        root:
          "min-h-11 shrink-0 flex-wrap gap-2 border-b border-border-subtle bg-sheet px-3 py-1.5",
      },
      inline: {
        root:
          "inline-flex gap-1 rounded-6 border border-border-subtle bg-inset px-1 py-0.5",
        group:
          "inline-flex gap-1 rounded-6 border border-border-subtle bg-inset px-1 py-0.5",
      },
      floating: {
        root:
          "inline-flex gap-1 rounded-6 border border-border-subtle bg-sheet px-1 py-0.5 text-fg shadow-sm",
        group:
          "inline-flex gap-1 rounded-6 border border-border-subtle bg-sheet px-1 py-0.5 text-fg shadow-sm",
      },
      plain: "",
    },
  },
  defaultVariants: {
    surface: "plain",
  },
});

export type ToolbarRecipeProps = VariantProps<typeof toolbarVariants>;

export type ToolbarRootProps = Omit<BaseToolbarRootProps, "className"> &
  Pick<ToolbarRecipeProps, "surface"> & {
    className?: string;
  };

/**
 * @owner Toolbar
 */
export const ToolbarRoot = React.forwardRef<HTMLDivElement, ToolbarRootProps>(
  function ToolbarRoot({ className, surface = "plain", ...props }, ref) {
    const styles = toolbarVariants({ surface });
    return (
      <BaseToolbar.Root
        ref={ref}
        className={styles.root({ className })}
        data-surface={surface}
        {...props}
      />
    );
  },
);
ToolbarRoot.displayName = "ToolbarRoot";

export type ToolbarGroupProps = Omit<BaseToolbarGroupProps, "className"> &
  Pick<ToolbarRecipeProps, "surface"> & {
    className?: string;
  };

/**
 * @owner Toolbar
 */
export const ToolbarGroup = React.forwardRef<HTMLDivElement, ToolbarGroupProps>(
  function ToolbarGroup({ className, surface = "plain", ...props }, ref) {
    const styles = toolbarVariants({ surface });
    return (
      <BaseToolbar.Group
        ref={ref}
        className={styles.group({ className })}
        data-surface={surface}
        {...props}
      />
    );
  },
);
ToolbarGroup.displayName = "ToolbarGroup";

export type ToolbarButtonProps = Omit<BaseToolbarButtonProps, "className"> &
  Pick<ButtonProps, "variant" | "size" | "pressed" | "badge" | "active" | "loading"> & {
    className?: string;
  };

/**
 * A toolbar item: the one `Button` recipe (a ghost `sm` by default; a ghost
 * `iconSm` is the icon control) registered with the toolbar's roving focus.
 * @owner Toolbar
 */
export const ToolbarButton = React.forwardRef<
  HTMLButtonElement,
  ToolbarButtonProps
>(function ToolbarButton(
  { variant = "ghost", size = "sm", pressed, badge, active, loading, className, render, ...props },
  ref,
) {
  // A caller's `render` is the item itself (a select trigger, a toggle); the Button is the default.
  return (
    <BaseToolbar.Button
      ref={ref}
      render={render ?? <Button variant={variant} size={size} pressed={pressed} badge={badge} active={active} loading={loading} className={className} />}
      {...props}
    />
  );
});
ToolbarButton.displayName = "ToolbarButton";

export type ToolbarSeparatorProps = Omit<
  BaseToolbarSeparatorProps,
  "className"
> & {
  className?: string;
};

/**
 * @owner Toolbar
 */
export const ToolbarSeparator = React.forwardRef<
  HTMLDivElement,
  ToolbarSeparatorProps
>(function ToolbarSeparator({ className, ...props }, ref) {
  const styles = toolbarVariants();
  return (
    <BaseToolbar.Separator
      ref={ref}
      className={styles.separator({ className })}
      {...props}
    />
  );
});
ToolbarSeparator.displayName = "ToolbarSeparator";

export type ToolbarSpacerProps = React.HTMLAttributes<HTMLDivElement> & {
  className?: string;
};

/**
 * @owner Toolbar
 */
export const ToolbarSpacer = React.forwardRef<
  HTMLDivElement,
  ToolbarSpacerProps
>(function ToolbarSpacer({ className, ...props }, ref) {
  const styles = toolbarVariants();
  return <div ref={ref} className={styles.spacer({ className })} {...props} />;
});
ToolbarSpacer.displayName = "ToolbarSpacer";

/**
 * @kind primitive
 */
export const Toolbar = Object.assign(ToolbarRoot, {
  Root: ToolbarRoot,
  Group: ToolbarGroup,
  Button: ToolbarButton,
  Separator: ToolbarSeparator,
  Spacer: ToolbarSpacer,
});
