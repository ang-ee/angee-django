import * as React from "react";
import { Menu as BaseMenu } from "@base-ui/react/menu";
import type {
  MenuPositionerProps as BaseMenuPositionerProps,
} from "@base-ui/react/menu";

import { cn } from "../lib/cn";
import {
  createMenuRecipe,
  createStyledMenuParts,
} from "./menu-parts";
import { PORTALED_CONTROL_LAYER } from "./popover";

/**
 * @kind token
 */
export const dropdownMenuVariants = createMenuRecipe();
export type DropdownMenuPositionerProps = Omit<
  BaseMenuPositionerProps,
  "className"
> & {
  className?: string;
};

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
/**
 * @owner DropdownMenu
 */
export const DropdownMenuContent = parts.Content;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuItem = parts.Item;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuLinkItem = parts.LinkItem;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuCheckboxItem = parts.CheckboxItem;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuCheckboxItemIndicator = parts.CheckboxItemIndicator;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuRadioItem = parts.RadioItem;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuRadioItemIndicator = parts.RadioItemIndicator;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuSubmenuTrigger = parts.SubmenuTrigger;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuLabel = parts.Label;
/**
 * @owner DropdownMenu
 */
export const DropdownMenuSeparator = parts.Separator;
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
