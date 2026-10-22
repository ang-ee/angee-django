import * as React from "react";
import { ContextMenu as BaseContextMenu } from "@base-ui/react/context-menu";
import type {
  ContextMenuArrowProps as BaseContextMenuArrowProps,
  ContextMenuBackdropProps as BaseContextMenuBackdropProps,
  ContextMenuGroupProps as BaseContextMenuGroupProps,
  ContextMenuPortalProps as BaseContextMenuPortalProps,
  ContextMenuPositionerProps as BaseContextMenuPositionerProps,
  ContextMenuRadioGroupProps as BaseContextMenuRadioGroupProps,
  ContextMenuRootProps as BaseContextMenuRootProps,
  ContextMenuSubmenuRootProps as BaseContextMenuSubmenuRootProps,
  ContextMenuTriggerProps as BaseContextMenuTriggerProps,
} from "@base-ui/react/context-menu";

import { cn } from "../lib/cn";
import {
  createMenuRecipe,
  createStyledMenuParts,
} from "./menu-parts";
import { PORTALED_CONTROL_LAYER } from "./popover";

// ContextMenu adds a styled `trigger` slot; everything else is the shared menu
// recipe (slots/variants/defaults), owned by `./menu-parts`.
/**
 * @kind token
 */
export const contextMenuVariants = createMenuRecipe({
  trigger: "outline-none focus-visible:focus-ring data-[popup-open]:focus-ring",
});
export type ContextMenuRootProps = BaseContextMenuRootProps;
export type ContextMenuPortalProps = BaseContextMenuPortalProps;
export type ContextMenuPositionerProps = Omit<
  BaseContextMenuPositionerProps,
  "className"
> & {
  className?: string;
};
export type ContextMenuArrowProps = BaseContextMenuArrowProps;
export type ContextMenuBackdropProps = BaseContextMenuBackdropProps;
export type ContextMenuGroupProps = BaseContextMenuGroupProps;
export type ContextMenuRadioGroupProps = BaseContextMenuRadioGroupProps;
export type ContextMenuSubmenuRootProps = BaseContextMenuSubmenuRootProps;

/**
 * @owner ContextMenu
 */
export const ContextMenuRoot = BaseContextMenu.Root;
/**
 * @owner ContextMenu
 */
export const ContextMenuPortal = BaseContextMenu.Portal;
/**
 * @owner ContextMenu
 */
export const ContextMenuArrow = BaseContextMenu.Arrow;
/**
 * @owner ContextMenu
 */
export const ContextMenuBackdrop = BaseContextMenu.Backdrop;
/**
 * @owner ContextMenu
 */
export const ContextMenuGroup = BaseContextMenu.Group;
/**
 * @owner ContextMenu
 */
export const ContextMenuRadioGroup = BaseContextMenu.RadioGroup;
/**
 * @owner ContextMenu
 */
export const ContextMenuSubmenuRoot = BaseContextMenu.SubmenuRoot;

/**
 * @owner ContextMenu
 */
export const ContextMenuPositioner = React.forwardRef<
  HTMLDivElement,
  ContextMenuPositionerProps
>(function ContextMenuPositioner({ className, ...props }, ref) {
  return (
    <BaseContextMenu.Positioner
      ref={ref}
      className={cn(PORTALED_CONTROL_LAYER, className)}
      {...props}
    />
  );
});
ContextMenuPositioner.displayName = "ContextMenuPositioner";

// ContextMenu carries its own styled Trigger slot (Menu/DropdownMenu have none).
export type ContextMenuTriggerProps = Omit<
  BaseContextMenuTriggerProps,
  "className"
> & {
  className?: string;
};

/**
 * @owner ContextMenu
 */
export const ContextMenuTrigger = React.forwardRef<
  HTMLDivElement,
  ContextMenuTriggerProps
>(function ContextMenuTrigger({ className, ...props }, ref) {
  const styles = contextMenuVariants();
  return (
    <BaseContextMenu.Trigger
      ref={ref}
      className={styles.trigger({ className })}
      {...props}
    />
  );
});
ContextMenuTrigger.displayName = "ContextMenuTrigger";

// The styled parts shared with DropdownMenu, built over the context recipe.
const parts = createStyledMenuParts(contextMenuVariants, "ContextMenu");
/**
 * @owner ContextMenu
 */
export const ContextMenuContent = parts.Content;
/**
 * @owner ContextMenu
 */
export const ContextMenuItem = parts.Item;
/**
 * @owner ContextMenu
 */
export const ContextMenuLinkItem = parts.LinkItem;
/**
 * @owner ContextMenu
 */
export const ContextMenuCheckboxItem = parts.CheckboxItem;
/**
 * @owner ContextMenu
 */
export const ContextMenuCheckboxItemIndicator = parts.CheckboxItemIndicator;
/**
 * @owner ContextMenu
 */
export const ContextMenuRadioItem = parts.RadioItem;
/**
 * @owner ContextMenu
 */
export const ContextMenuRadioItemIndicator = parts.RadioItemIndicator;
/**
 * @owner ContextMenu
 */
export const ContextMenuSubmenuTrigger = parts.SubmenuTrigger;
/**
 * @owner ContextMenu
 */
export const ContextMenuLabel = parts.Label;
/**
 * @owner ContextMenu
 */
export const ContextMenuSeparator = parts.Separator;
/**
 * @owner ContextMenu
 */
export const ContextMenuShortcut = parts.Shortcut;

/**
 * @kind primitive
 */
export const ContextMenu = Object.assign(ContextMenuRoot, {
  Root: ContextMenuRoot,
  Trigger: ContextMenuTrigger,
  Portal: ContextMenuPortal,
  Positioner: ContextMenuPositioner,
  Content: ContextMenuContent,
  Item: ContextMenuItem,
  LinkItem: ContextMenuLinkItem,
  CheckboxItem: ContextMenuCheckboxItem,
  CheckboxItemIndicator: ContextMenuCheckboxItemIndicator,
  RadioGroup: ContextMenuRadioGroup,
  RadioItem: ContextMenuRadioItem,
  RadioItemIndicator: ContextMenuRadioItemIndicator,
  SubmenuRoot: ContextMenuSubmenuRoot,
  SubmenuTrigger: ContextMenuSubmenuTrigger,
  Label: ContextMenuLabel,
  Separator: ContextMenuSeparator,
  Shortcut: ContextMenuShortcut,
  Arrow: ContextMenuArrow,
  Backdrop: ContextMenuBackdrop,
  Group: ContextMenuGroup,
});
