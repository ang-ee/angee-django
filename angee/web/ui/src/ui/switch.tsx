import * as React from "react";
import { Switch as BaseSwitch } from "@base-ui/react/switch";
import type {
  SwitchRootProps as BaseSwitchRootProps,
  SwitchThumbProps as BaseSwitchThumbProps,
} from "@base-ui/react/switch";

import { tv, type VariantProps } from "../lib/variants";
import { textRoleVariants } from "./text";

export const switchVariants = tv({
  slots: {
    root: "relative inline-flex shrink-0 cursor-pointer items-center rounded-full bg-border-strong outline-none transition-colors focus-visible:focus-ring data-[checked]:bg-brand data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60",
    thumb:
      "pointer-events-none block translate-x-0.5 rounded-full bg-sheet shadow-sm transition-transform data-[checked]:translate-x-4",
    row: "flex items-center justify-between gap-3 rounded-6 border border-border-subtle bg-sheet px-3 py-2",
    rowText: "min-w-0 space-y-0.5",
    label: "text-13 font-medium text-fg",
    description: textRoleVariants({ role: "caption" }),
  },
  variants: {
    size: {
      sm: {
        root: "h-5 w-9",
        thumb: "size-4",
      },
      md: {
        root: "h-6 w-10",
        thumb: "size-5",
      },
    },
  },
  defaultVariants: {
    size: "sm",
  },
});

type SwitchRecipeProps = VariantProps<typeof switchVariants>;

export type SwitchSize = NonNullable<SwitchRecipeProps["size"]>;

export type SwitchRootProps = Omit<BaseSwitchRootProps, "className"> &
  SwitchRecipeProps & {
    className?: string;
  };

export const SwitchRoot = React.forwardRef<HTMLElement, SwitchRootProps>(
  function SwitchRoot({ className, size = "sm", ...props }, ref) {
    const styles = switchVariants({ size });
    return (
      <BaseSwitch.Root
        ref={ref}
        className={styles.root({ className })}
        {...props}
      />
    );
  },
);
SwitchRoot.displayName = "SwitchRoot";

export type SwitchThumbProps = Omit<BaseSwitchThumbProps, "className"> &
  SwitchRecipeProps & {
    className?: string;
  };

export const SwitchThumb = React.forwardRef<
  HTMLSpanElement,
  SwitchThumbProps
>(function SwitchThumb({ className, size = "sm", ...props }, ref) {
  const styles = switchVariants({ size });
  return (
    <BaseSwitch.Thumb
      ref={ref}
      className={styles.thumb({ className })}
      {...props}
    />
  );
});
SwitchThumb.displayName = "SwitchThumb";

export type SwitchProps = Omit<SwitchRootProps, "children"> & {
  thumbClassName?: string;
};

export const Switch = React.forwardRef<HTMLElement, SwitchProps>(
  function Switch({ size = "sm", thumbClassName, ...props }, ref) {
    return (
      <SwitchRoot ref={ref} size={size} {...props}>
        <SwitchThumb size={size} className={thumbClassName} />
      </SwitchRoot>
    );
  },
);
Switch.displayName = "Switch";

export type SwitchRowProps = Omit<SwitchProps, "className"> & {
  label: React.ReactNode;
  description?: React.ReactNode;
  className?: string;
  switchClassName?: string;
};

export const SwitchRow = React.forwardRef<HTMLElement, SwitchRowProps>(
  function SwitchRow(
    { label, description, className, switchClassName, ...props },
    ref,
  ) {
    const styles = switchVariants();
    return (
      <label className={styles.row({ className })}>
        <span className={styles.rowText()}>
          <span className={styles.label()}>{label}</span>
          {description ? (
            <span className={styles.description()}>{description}</span>
          ) : null}
        </span>
        <Switch ref={ref} className={switchClassName} {...props} />
      </label>
    );
  },
);
SwitchRow.displayName = "SwitchRow";
