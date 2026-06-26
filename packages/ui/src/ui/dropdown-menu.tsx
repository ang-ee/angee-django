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

export const dropdownMenuVariants = createMenuRecipe();

export type DropdownMenuItemVariant = MenuItemVariant;
export type DropdownMenuRootProps<Payload = unknown> =
  BaseMenuRootProps<Payload>;
export type DropdownMenuTriggerProps<Payload = unknown> =
  BaseMenuTriggerProps<Payload>;
export type DropdownMenuPortalProps = BaseMenuPortalProps;
export type DropdownMenuPositionerProps = BaseMenuPositionerProps;
export type DropdownMenuArrowProps = BaseMenuArrowProps;
export type DropdownMenuBackdropProps = BaseMenuBackdropProps;
export type DropdownMenuGroupProps = BaseMenuGroupProps;
export type DropdownMenuRadioGroupProps = BaseMenuRadioGroupProps;
export type DropdownMenuSubmenuRootProps = BaseMenuSubmenuRootProps;
export type DropdownMenuViewportProps = React.ComponentPropsWithoutRef<
  typeof BaseMenu.Viewport
>;

export const DropdownMenuRoot = BaseMenu.Root;
export const DropdownMenuTrigger = BaseMenu.Trigger;
export const DropdownMenuPortal = BaseMenu.Portal;
export const DropdownMenuPositioner = BaseMenu.Positioner;
export const DropdownMenuArrow = BaseMenu.Arrow;
export const DropdownMenuBackdrop = BaseMenu.Backdrop;
export const DropdownMenuGroup = BaseMenu.Group;
export const DropdownMenuRadioGroup = BaseMenu.RadioGroup;
export const DropdownMenuSubmenuRoot = BaseMenu.SubmenuRoot;
export const DropdownMenuViewport = BaseMenu.Viewport;

// The styled parts shared with ContextMenu, built over the dropdown recipe.
const parts = createStyledMenuParts(dropdownMenuVariants, "DropdownMenu");

export type DropdownMenuContentProps = MenuContentProps;
export const DropdownMenuContent = parts.Content;

export type DropdownMenuItemProps = MenuItemProps;
export const DropdownMenuItem = parts.Item;

export type DropdownMenuLinkItemProps = MenuLinkItemProps;
export const DropdownMenuLinkItem = parts.LinkItem;

export type DropdownMenuCheckboxItemProps = MenuCheckboxItemProps;
export const DropdownMenuCheckboxItem = parts.CheckboxItem;

export type DropdownMenuCheckboxItemIndicatorProps =
  MenuCheckboxItemIndicatorProps;
export const DropdownMenuCheckboxItemIndicator = parts.CheckboxItemIndicator;

export type DropdownMenuRadioItemProps = MenuRadioItemProps;
export const DropdownMenuRadioItem = parts.RadioItem;

export type DropdownMenuRadioItemIndicatorProps = MenuRadioItemIndicatorProps;
export const DropdownMenuRadioItemIndicator = parts.RadioItemIndicator;

export type DropdownMenuSubmenuTriggerProps = MenuSubmenuTriggerProps;
export const DropdownMenuSubmenuTrigger = parts.SubmenuTrigger;

export type DropdownMenuLabelProps = MenuLabelProps;
export const DropdownMenuLabel = parts.Label;

export type DropdownMenuSeparatorProps = MenuSeparatorProps;
export const DropdownMenuSeparator = parts.Separator;

export type DropdownMenuShortcutProps = MenuShortcutProps;
export const DropdownMenuShortcut = parts.Shortcut;

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
