import * as React from "react";
import {
  ToggleGroup as BaseToggleGroup,
  type ToggleGroupChangeEventDetails,
  type ToggleGroupProps as BaseToggleGroupProps,
} from "@base-ui/react/toggle-group";

import { tv, type VariantProps } from "../lib/variants";
import { Toggle, type ToggleProps } from "./toggle";

export const toggleGroupVariants = tv({
  slots: {
    root:
      "inline-flex min-w-0 items-center data-[orientation=vertical]:flex-col data-[orientation=vertical]:items-stretch",
    item:
      "min-w-0 text-fg-muted hover:text-fg disabled:opacity-50 data-[pressed]:text-fg",
  },
  variants: {
    variant: {
      segmented: {
        root: "gap-0.5 rounded-6 border border-border-subtle bg-inset p-0.5",
        item: "rounded-6 border border-transparent data-[pressed]:bg-sheet data-[pressed]:shadow-xs",
      },
      toolbar: {
        root: "gap-0.5 rounded-6 bg-sheet/80 p-0.5",
        item: "rounded-6 data-[pressed]:bg-brand data-[pressed]:text-on-brand data-[pressed]:hover:bg-brand data-[pressed]:hover:text-on-brand",
      },
      card: {
        root: "gap-3",
        item: "rounded-6 text-fg hover:text-fg data-[pressed]:text-fg",
      },
    },
    size: {
      xs: {
        item: "h-5 px-1.5 text-2xs",
      },
      sm: {
        item: "h-6 px-2 text-2xs",
      },
      md: {
        item: "h-8 px-2.5 text-13",
      },
    },
  },
  defaultVariants: {
    variant: "segmented",
    size: "sm",
  },
});

type ToggleGroupRecipeProps = VariantProps<typeof toggleGroupVariants>;

export type ToggleGroupVariant = NonNullable<
  ToggleGroupRecipeProps["variant"]
>;
export type ToggleGroupSize = NonNullable<ToggleGroupRecipeProps["size"]>;

type ToggleGroupContextValue = {
  variant: ToggleGroupVariant;
  size: ToggleGroupSize;
};

const ToggleGroupContext = React.createContext<ToggleGroupContextValue>({
  variant: "segmented",
  size: "sm",
});

export type ToggleGroupRootProps = Omit<
  BaseToggleGroupProps<string>,
  "className"
> &
  ToggleGroupRecipeProps & {
    className?: string;
  };

export const ToggleGroupRoot = React.forwardRef<
  HTMLDivElement,
  ToggleGroupRootProps
>(function ToggleGroupRoot(
  { className, variant = "segmented", size = "sm", ...props },
  ref,
) {
  const styles = toggleGroupVariants({ variant, size });
  const contextValue = React.useMemo(
    () => ({ variant, size }),
    [variant, size],
  );
  return (
    <ToggleGroupContext.Provider value={contextValue}>
      <BaseToggleGroup
        ref={ref}
        className={styles.root({ className })}
        {...props}
      />
    </ToggleGroupContext.Provider>
  );
});
ToggleGroupRoot.displayName = "ToggleGroupRoot";

export type ToggleGroupItemProps = Omit<
  ToggleProps,
  "className" | "variant" | "size"
> &
  ToggleGroupRecipeProps & {
    className?: string;
  };

export const ToggleGroupItem = React.forwardRef<
  HTMLButtonElement,
  ToggleGroupItemProps
>(function ToggleGroupItem({ className, variant, size, ...props }, ref) {
  const context = React.useContext(ToggleGroupContext);
  const resolvedVariant = variant ?? context.variant;
  const resolvedSize = size ?? context.size;
  const styles = toggleGroupVariants({
    variant: resolvedVariant,
    size: resolvedSize,
  });
  return (
    <Toggle
      ref={ref}
      variant="ghost"
      size="sm"
      className={styles.item({ className })}
      {...props}
    />
  );
});
ToggleGroupItem.displayName = "ToggleGroupItem";

export const ToggleGroup = Object.assign(ToggleGroupRoot, {
  Root: ToggleGroupRoot,
  Item: ToggleGroupItem,
});

export interface SegmentedControlOption<Value extends string = string> {
  value: Value;
  label: React.ReactNode;
  disabled?: boolean;
  title?: string;
  ariaLabel?: string;
}

export type SegmentedControlProps<Value extends string = string> = Omit<
  ToggleGroupRootProps,
  "children" | "defaultValue" | "multiple" | "onValueChange" | "value"
> & {
  options: readonly SegmentedControlOption<Value>[];
  value?: Value;
  defaultValue?: Value;
  allowEmpty?: boolean;
  onValueChange?: (
    value: Value,
    eventDetails: ToggleGroupChangeEventDetails,
  ) => void;
};

export function SegmentedControl<Value extends string = string>({
  options,
  value,
  defaultValue,
  allowEmpty = false,
  onValueChange,
  ...props
}: SegmentedControlProps<Value>) {
  function handleValueChange(
    nextValues: string[],
    eventDetails: ToggleGroupChangeEventDetails,
  ) {
    const nextValue = nextValues.at(-1) as Value | undefined;
    if (!nextValue && !allowEmpty) return;
    onValueChange?.((nextValue ?? "") as Value, eventDetails);
  }

  return (
    <ToggleGroupRoot
      value={value ? [value] : []}
      defaultValue={defaultValue ? [defaultValue] : undefined}
      multiple={false}
      onValueChange={handleValueChange}
      {...props}
    >
      {options.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          disabled={option.disabled}
          title={option.title}
          aria-label={option.ariaLabel}
        >
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroupRoot>
  );
}
