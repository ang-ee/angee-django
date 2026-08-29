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

// ContextMenu adds a styled `trigger` slot; everything else is the shared menu
// recipe (slots/variants/defaults), owned by `./menu-parts`.
export const contextMenuVariants = createMenuRecipe({
  trigger: "outline-none focus-visible:focus-ring data-[popup-open]:focus-ring",
});

export type ContextMenuItemVariant = MenuItemVariant;
export type ContextMenuRootProps = BaseContextMenuRootProps;
export type ContextMenuPortalProps = BaseContextMenuPortalProps;
export type ContextMenuPositionerProps = BaseContextMenuPositionerProps;
export type ContextMenuArrowProps = BaseContextMenuArrowProps;
export type ContextMenuBackdropProps = BaseContextMenuBackdropProps;
export type ContextMenuGroupProps = BaseContextMenuGroupProps;
export type ContextMenuRadioGroupProps = BaseContextMenuRadioGroupProps;
export type ContextMenuSubmenuRootProps = BaseContextMenuSubmenuRootProps;

export const ContextMenuRoot = BaseContextMenu.Root;
export const ContextMenuPortal = BaseContextMenu.Portal;
export const ContextMenuPositioner = BaseContextMenu.Positioner;
export const ContextMenuArrow = BaseContextMenu.Arrow;
export const ContextMenuBackdrop = BaseContextMenu.Backdrop;
export const ContextMenuGroup = BaseContextMenu.Group;
export const ContextMenuRadioGroup = BaseContextMenu.RadioGroup;
export const ContextMenuSubmenuRoot = BaseContextMenu.SubmenuRoot;

// ContextMenu carries its own styled Trigger slot (Menu/DropdownMenu have none).
export type ContextMenuTriggerProps = Omit<
  BaseContextMenuTriggerProps,
  "className"
> & {
  className?: string;
};

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

export type ContextMenuContentProps = MenuContentProps;
export const ContextMenuContent = parts.Content;

export type ContextMenuItemProps = MenuItemProps;
export const ContextMenuItem = parts.Item;

export type ContextMenuLinkItemProps = MenuLinkItemProps;
export const ContextMenuLinkItem = parts.LinkItem;

export type ContextMenuCheckboxItemProps = MenuCheckboxItemProps;
export const ContextMenuCheckboxItem = parts.CheckboxItem;

export type ContextMenuCheckboxItemIndicatorProps =
  MenuCheckboxItemIndicatorProps;
export const ContextMenuCheckboxItemIndicator = parts.CheckboxItemIndicator;

export type ContextMenuRadioItemProps = MenuRadioItemProps;
export const ContextMenuRadioItem = parts.RadioItem;

export type ContextMenuRadioItemIndicatorProps = MenuRadioItemIndicatorProps;
export const ContextMenuRadioItemIndicator = parts.RadioItemIndicator;

export type ContextMenuSubmenuTriggerProps = MenuSubmenuTriggerProps;
export const ContextMenuSubmenuTrigger = parts.SubmenuTrigger;

export type ContextMenuLabelProps = MenuLabelProps;
export const ContextMenuLabel = parts.Label;

export type ContextMenuSeparatorProps = MenuSeparatorProps;
export const ContextMenuSeparator = parts.Separator;

export type ContextMenuShortcutProps = MenuShortcutProps;
export const ContextMenuShortcut = parts.Shortcut;

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
