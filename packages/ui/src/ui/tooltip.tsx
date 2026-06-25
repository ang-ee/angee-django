import * as React from "react";
import { Tooltip as BaseTooltip } from "@base-ui/react/tooltip";
import type {
  TooltipArrowProps as BaseTooltipArrowProps,
  TooltipPopupProps as BaseTooltipPopupProps,
  TooltipPositionerProps as BaseTooltipPositionerProps,
  TooltipPortalProps as BaseTooltipPortalProps,
  TooltipProviderProps as BaseTooltipProviderProps,
  TooltipRootProps as BaseTooltipRootProps,
  TooltipTriggerProps as BaseTooltipTriggerProps,
} from "@base-ui/react/tooltip";

import { tv, type VariantProps } from "../lib/variants";

export const tooltipVariants = tv({
  slots: {
    content:
      "z-tooltip max-w-xs rounded-md bg-tooltip px-2 py-1.5 text-2xs font-medium text-on-tooltip shadow-md outline-none transition-opacity data-[ending-style]:opacity-0 data-[starting-style]:opacity-0",
    arrow: "text-tooltip data-[uncentered]:hidden",
  },
  variants: {
    size: {
      sm: {
        content: "px-2 py-1 text-2xs",
      },
      md: {
        content: "px-2 py-1.5 text-2xs",
      },
    },
  },
  defaultVariants: {
    size: "md",
  },
});

type TooltipRecipeProps = VariantProps<typeof tooltipVariants>;

export type TooltipSize = NonNullable<TooltipRecipeProps["size"]>;
export type TooltipSide = NonNullable<BaseTooltipPositionerProps["side"]>;
export type TooltipRootProps<Payload = unknown> =
  BaseTooltipRootProps<Payload>;
export type TooltipTriggerProps<Payload = unknown> =
  BaseTooltipTriggerProps<Payload>;
export type TooltipPortalProps = BaseTooltipPortalProps;
export type TooltipPositionerProps = BaseTooltipPositionerProps;
export type TooltipProviderProps = BaseTooltipProviderProps;
export type TooltipArrowProps = BaseTooltipArrowProps & {
  className?: string;
};

export const TooltipRoot = BaseTooltip.Root;
export const TooltipTrigger = BaseTooltip.Trigger;
export const TooltipPortal = BaseTooltip.Portal;
export const TooltipPositioner = BaseTooltip.Positioner;
export const TooltipProvider = BaseTooltip.Provider;

export type TooltipContentProps = BaseTooltipPopupProps &
  Pick<TooltipRecipeProps, "size"> & {
    className?: string;
  };

export const TooltipContent = React.forwardRef<
  HTMLDivElement,
  TooltipContentProps
>(function TooltipContent({ className, size = "md", ...props }, ref) {
  const styles = tooltipVariants({ size });
  return (
    <BaseTooltip.Popup
      ref={ref}
      className={styles.content({ className })}
      {...props}
    />
  );
});
TooltipContent.displayName = "TooltipContent";

export const TooltipPopup = TooltipContent;

export const TooltipArrow = React.forwardRef<
  HTMLDivElement,
  TooltipArrowProps
>(function TooltipArrow({ className, ...props }, ref) {
  const styles = tooltipVariants();
  return (
    <BaseTooltip.Arrow
      ref={ref}
      className={styles.arrow({ className })}
      {...props}
    />
  );
});
TooltipArrow.displayName = "TooltipArrow";

export type TooltipProps = Omit<TooltipRootProps, "children"> &
  Pick<
    TooltipPositionerProps,
    "align" | "alignOffset" | "collisionPadding" | "side" | "sideOffset"
  > &
  Pick<TooltipTriggerProps, "closeDelay" | "delay"> &
  Pick<TooltipContentProps, "size"> & {
    children: React.ReactElement<Record<string, unknown>>;
    contentClassName?: string;
    label: React.ReactNode;
    positionerClassName?: string;
  };

export const Tooltip = function Tooltip({
  children,
  closeDelay,
  contentClassName,
  delay,
  label,
  positionerClassName,
  side = "top",
  sideOffset = 8,
  align,
  alignOffset,
  collisionPadding,
  size,
  disabled,
  ...props
}: TooltipProps) {
  if (label === null || label === undefined || label === "") {
    return children;
  }

  return (
    <TooltipRoot disabled={disabled} {...props}>
      <TooltipTrigger
        closeDelay={closeDelay}
        delay={delay}
        render={children}
      />
      <TooltipPortal>
        <TooltipPositioner
          align={align}
          alignOffset={alignOffset}
          collisionPadding={collisionPadding}
          className={positionerClassName}
          side={side}
          sideOffset={sideOffset}
        >
          <TooltipContent className={contentClassName} size={size}>
            {label}
          </TooltipContent>
        </TooltipPositioner>
      </TooltipPortal>
    </TooltipRoot>
  );
};

