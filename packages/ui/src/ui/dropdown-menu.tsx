import * as React from "react";
import { Menu as BaseMenu } from "@base-ui/react/menu";
import type {
  MenuArrowProps as BaseMenuArrowProps,
  MenuBackdropProps as BaseMenuBackdropProps,
  MenuGroupProps as BaseMenuGroupProps,
  MenuPortalProps as BaseMenuPortalProps,
  MenuPositionerProps as BaseMenuPositionerProps,
  MenuRadioGroupProps as BaseMenuRadioGroupProps,
  MenuRootProps as BaseMenuRootProps,
  MenuSubmenuRootProps as BaseMenuSubmenuRootProps,
  MenuTriggerProps as BaseMenuTriggerProps,
} from "@base-ui/react/menu";

import { cn } from "../lib/cn";
import {
  createMenuRecipe,
  createStyledMenuParts,
  type MenuCheckboxItemIndicatorProps,
  type MenuCheckboxItemProps,
  type MenuContentProps,
  type MenuItemProps,
  type MenuItemVariant,
  type MenuLabelProps,
  type MenuLinkItemProps,
  type MenuRadioItemIndicatorProps,
  type MenuRadioItemProps,
  type MenuSeparatorProps,
  type MenuShortcutProps,
  type MenuSubmenuTriggerProps,
} from "./menu-parts";
import { PORTALED_CONTROL_LAYER } from "./popover";

/**
 * @kind token
 */
export const dropdownMenuVariants = createMenuRecipe();

export type DropdownMenuItemVariant = MenuItemVariant;
export type DropdownMenuRootProps<Payload = unknown> =
  BaseMenuRootProps<Payload>;
export type DropdownMenuTriggerProps<Payload = unknown> =
  BaseMenuTriggerProps<Payload>;
export type DropdownMenuPortalProps = BaseMenuPortalProps;
export type DropdownMenuPositionerProps = Omit<
  BaseMenuPositionerProps,
  "className"
> & {
  className?: string;
};
export type DropdownMenuArrowProps = BaseMenuArrowProps;
export type DropdownMenuBackdropProps = BaseMenuBackdropProps;
export type DropdownMenuGroupProps = BaseMenuGroupProps;
export type DropdownMenuRadioGroupProps = BaseMenuRadioGroupProps;
export type DropdownMenuSubmenuRootProps = BaseMenuSubmenuRootProps;
export type DropdownMenuViewportProps = React.ComponentPropsWithoutRef<
  typeof BaseMenu.Viewport
>;

/**
 * @owner DropdownMenu
 */
export const DropdownMenuRoot = BaseMenu.Root;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuTrigger = BaseMenu.Trigger;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuPortal = BaseMenu.Portal;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuArrow = BaseMenu.Arrow;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuBackdrop = BaseMenu.Backdrop;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuGroup = BaseMenu.Group;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuRadioGroup = BaseMenu.RadioGroup;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuSubmenuRoot = BaseMenu.SubmenuRoot;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuViewport = BaseMenu.Viewport;

/**
 * @owner DropdownMenu
 */
export const DropdownMenuPositioner = React.forwardRef<
  HTMLDivElement,
  DropdownMenuPositionerProps
>(function DropdownMenuPositioner({ className, ...props }, ref) {
  return (
    <BaseMenu.Positioner
      ref={ref}
      className={cn(PORTALED_CONTROL_LAYER, className)}
      {...props}
    />
  );
});
DropdownMenuPositioner.displayName = "DropdownMenuPositioner";

// The styled parts shared with ContextMenu, built over the dropdown recipe.
const parts = createStyledMenuParts(dropdownMenuVariants, "DropdownMenu");

export type DropdownMenuContentProps = MenuContentProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuContent = parts.Content;

export type DropdownMenuItemProps = MenuItemProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuItem = parts.Item;

export type DropdownMenuLinkItemProps = MenuLinkItemProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuLinkItem = parts.LinkItem;

export type DropdownMenuCheckboxItemProps = MenuCheckboxItemProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuCheckboxItem = parts.CheckboxItem;

export type DropdownMenuCheckboxItemIndicatorProps =
  MenuCheckboxItemIndicatorProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuCheckboxItemIndicator = parts.CheckboxItemIndicator;

export type DropdownMenuRadioItemProps = MenuRadioItemProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuRadioItem = parts.RadioItem;

export type DropdownMenuRadioItemIndicatorProps = MenuRadioItemIndicatorProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuRadioItemIndicator = parts.RadioItemIndicator;

export type DropdownMenuSubmenuTriggerProps = MenuSubmenuTriggerProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuSubmenuTrigger = parts.SubmenuTrigger;

export type DropdownMenuLabelProps = MenuLabelProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuLabel = parts.Label;

export type DropdownMenuSeparatorProps = MenuSeparatorProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuSeparator = parts.Separator;

export type DropdownMenuShortcutProps = MenuShortcutProps;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuShortcut = parts.Shortcut;

/**
 * @kind primitive
 */
export const DropdownMenu = Object.assign(DropdownMenuRoot, {
  Root: DropdownMenuRoot,
  Trigger: DropdownMenuTrigger,
  Portal: DropdownMenuPortal,
  Positioner: DropdownMenuPositioner,
  Content: DropdownMenuContent,
  Item: DropdownMenuItem,
  LinkItem: DropdownMenuLinkItem,
  CheckboxItem: DropdownMenuCheckboxItem,
  CheckboxItemIndicator: DropdownMenuCheckboxItemIndicator,
  RadioGroup: DropdownMenuRadioGroup,
  RadioItem: DropdownMenuRadioItem,
  RadioItemIndicator: DropdownMenuRadioItemIndicator,
  SubmenuRoot: DropdownMenuSubmenuRoot,
  SubmenuTrigger: DropdownMenuSubmenuTrigger,
  Label: DropdownMenuLabel,
  Separator: DropdownMenuSeparator,
  Shortcut: DropdownMenuShortcut,
  Arrow: DropdownMenuArrow,
  Backdrop: DropdownMenuBackdrop,
  Group: DropdownMenuGroup,
  Viewport: DropdownMenuViewport,
});
