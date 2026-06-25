import * as React from "react";
import { Toolbar as BaseToolbar } from "@base-ui/react/toolbar";
import type {
  ToolbarButtonProps as BaseToolbarButtonProps,
  ToolbarGroupProps as BaseToolbarGroupProps,
  ToolbarInputProps as BaseToolbarInputProps,
  ToolbarRootProps as BaseToolbarRootProps,
  ToolbarSeparatorProps as BaseToolbarSeparatorProps,
} from "@base-ui/react/toolbar";

import { tv, type VariantProps } from "../lib/variants";

export const toolbarVariants = tv({
  slots: {
    root: "flex min-w-0 items-center gap-2",
    group: "flex min-w-0 items-center gap-2",
    button:
      "inline-flex items-center justify-center gap-1.5 rounded text-13 font-medium text-fg-2 outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-50 data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 [&_.glyph]:size-3.5",
    input:
      "h-8 min-w-0 rounded border border-border bg-sheet px-2 text-13 text-fg outline-none transition-colors placeholder:text-fg-subtle focus:border-border-focus focus:focus-ring disabled:cursor-not-allowed disabled:opacity-50",
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
          "inline-flex gap-1 rounded-md border border-border-subtle bg-inset px-1 py-0.5",
        group:
          "inline-flex gap-1 rounded-md border border-border-subtle bg-inset px-1 py-0.5",
      },
      floating: {
        root:
          "inline-flex gap-1 rounded-md border border-border-subtle bg-sheet px-1 py-0.5 text-fg shadow-sm",
        group:
          "inline-flex gap-1 rounded-md border border-border-subtle bg-sheet px-1 py-0.5 text-fg shadow-sm",
      },
      plain: "",
    },
    buttonSize: {
      sm: { button: "h-7 px-2 text-13" },
      md: { button: "h-8 px-2.5 text-13" },
      iconSm: { button: "size-icon-btn-sm px-0" },
      iconMd: { button: "size-icon-btn-md px-0" },
    },
    buttonTone: {
      ghost: { button: "text-fg-2 hover:bg-inset hover:text-fg" },
      secondary: {
        button:
          "border border-border-strong bg-inset text-fg hover:bg-sheet",
      },
      primary: {
        button: "bg-brand text-on-brand hover:bg-brand-hover",
      },
    },
  },
  defaultVariants: {
    surface: "plain",
    buttonSize: "sm",
    buttonTone: "ghost",
  },
});

export type ToolbarRecipeProps = VariantProps<typeof toolbarVariants>;
export type ToolbarSurface = NonNullable<ToolbarRecipeProps["surface"]>;
export type ToolbarButtonSize =
  NonNullable<ToolbarRecipeProps["buttonSize"]>;
export type ToolbarButtonTone =
  NonNullable<ToolbarRecipeProps["buttonTone"]>;

export type ToolbarRootProps = Omit<BaseToolbarRootProps, "className"> &
  Pick<ToolbarRecipeProps, "surface"> & {
    className?: string;
  };

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
  Pick<ToolbarRecipeProps, "buttonSize" | "buttonTone"> & {
    className?: string;
  };

export const ToolbarButton = React.forwardRef<
  HTMLButtonElement,
  ToolbarButtonProps
>(function ToolbarButton(
  { buttonSize = "sm", buttonTone = "ghost", className, ...props },
  ref,
) {
  const styles = toolbarVariants({ buttonSize, buttonTone });
  return (
    <BaseToolbar.Button
      ref={ref}
      className={styles.button({ className })}
      {...props}
    />
  );
});
ToolbarButton.displayName = "ToolbarButton";

export type ToolbarInputProps = Omit<BaseToolbarInputProps, "className"> & {
  className?: string;
};

export const ToolbarInput = React.forwardRef<
  HTMLInputElement,
  ToolbarInputProps
>(function ToolbarInput({ className, ...props }, ref) {
  const styles = toolbarVariants();
  return (
    <BaseToolbar.Input
      ref={ref}
      className={styles.input({ className })}
      {...props}
    />
  );
});
ToolbarInput.displayName = "ToolbarInput";

export type ToolbarSeparatorProps = Omit<
  BaseToolbarSeparatorProps,
  "className"
> & {
  className?: string;
};

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

export const ToolbarSpacer = React.forwardRef<
  HTMLDivElement,
  ToolbarSpacerProps
>(function ToolbarSpacer({ className, ...props }, ref) {
  const styles = toolbarVariants();
  return <div ref={ref} className={styles.spacer({ className })} {...props} />;
});
ToolbarSpacer.displayName = "ToolbarSpacer";

export const Toolbar = Object.assign(ToolbarRoot, {
  Root: ToolbarRoot,
  Group: ToolbarGroup,
  Button: ToolbarButton,
  Input: ToolbarInput,
  Separator: ToolbarSeparator,
  Spacer: ToolbarSpacer,
});
