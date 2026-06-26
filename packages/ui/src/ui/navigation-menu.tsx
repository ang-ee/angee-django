import * as React from "react";
import { NavigationMenu as BaseNavigationMenu } from "@base-ui/react/navigation-menu";
import type {
  NavigationMenuArrowProps as BaseNavigationMenuArrowProps,
  NavigationMenuBackdropProps as BaseNavigationMenuBackdropProps,
  NavigationMenuContentProps as BaseNavigationMenuContentProps,
  NavigationMenuIconProps as BaseNavigationMenuIconProps,
  NavigationMenuItemProps as BaseNavigationMenuItemProps,
  NavigationMenuLinkProps as BaseNavigationMenuLinkProps,
  NavigationMenuListProps as BaseNavigationMenuListProps,
  NavigationMenuPopupProps as BaseNavigationMenuPopupProps,
  NavigationMenuPortalProps as BaseNavigationMenuPortalProps,
  NavigationMenuPositionerProps as BaseNavigationMenuPositionerProps,
  NavigationMenuRootProps as BaseNavigationMenuRootProps,
  NavigationMenuTriggerProps as BaseNavigationMenuTriggerProps,
  NavigationMenuViewportProps as BaseNavigationMenuViewportProps,
} from "@base-ui/react/navigation-menu";

import { Glyph } from "../chrome/Glyph";
import { tv, type VariantProps } from "../lib/variants";
import { POPUP_BASE } from "./popover";

export const navigationMenuVariants = tv({
  slots: {
    root: "flex min-w-0 items-center",
    list: "flex min-w-0 items-center gap-0.5",
    item: "relative min-w-0",
    link:
      "inline-flex h-7 min-w-0 items-center gap-1.5 rounded-md pl-3 text-13 font-medium text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[active]:bg-rail-hi data-[active]:text-on-rail-hi [&>.glyph]:size-3 [&>.glyph]:shrink-0 [&>.glyph]:opacity-80 hover:[&>.glyph]:opacity-100",
    trigger:
      "inline-flex h-7 min-w-0 items-center gap-1.5 rounded-md pl-3 text-13 font-medium text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[popup-open]:bg-rail-hi data-[popup-open]:text-on-rail-hi [&>.glyph]:size-3 [&>.glyph]:shrink-0 [&>.glyph]:opacity-80 hover:[&>.glyph]:opacity-100",
    text:
      "inline-flex h-7 min-w-0 cursor-default items-center gap-1.5 rounded-md pl-3 text-13 font-medium text-on-rail-mut [&>.glyph]:size-3 [&>.glyph]:shrink-0 [&>.glyph]:opacity-80",
    content: "p-2",
    popup: POPUP_BASE,
    viewport: "overflow-hidden",
    icon:
      "ml-0.5 flex size-3 shrink-0 items-center justify-center text-current transition-transform data-[popup-open]:rotate-180 [&_.glyph]:size-3",
    arrow:
      "flex size-2.5 rotate-45 border-l border-t border-border-subtle bg-popover",
    backdrop: "fixed inset-0 bg-transparent",
  },
  variants: {
    active: {
      true: {
        link: "bg-rail-hi text-on-rail-hi [&>.glyph]:opacity-100",
        trigger: "bg-rail-hi text-on-rail-hi [&>.glyph]:opacity-100",
        text: "bg-rail-hi text-on-rail-hi [&>.glyph]:opacity-100",
      },
      false: "",
    },
    hasPopup: {
      true: {
        link: "pr-2",
        trigger: "pr-2",
        text: "pr-2",
      },
      false: {
        link: "pr-3",
        trigger: "pr-3",
        text: "pr-3",
      },
    },
  },
  defaultVariants: {
    active: false,
    hasPopup: false,
  },
});

export type NavigationMenuRecipeProps = VariantProps<
  typeof navigationMenuVariants
>;

export type NavigationMenuRootProps = Omit<
  BaseNavigationMenuRootProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuRoot = React.forwardRef<
  HTMLElement,
  NavigationMenuRootProps
>(function NavigationMenuRoot({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Root
      ref={ref}
      className={styles.root({ className })}
      {...props}
    />
  );
});
NavigationMenuRoot.displayName = "NavigationMenuRoot";

export type NavigationMenuListProps = Omit<
  BaseNavigationMenuListProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuList = React.forwardRef<
  HTMLUListElement,
  NavigationMenuListProps
>(function NavigationMenuList({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.List
      ref={ref}
      className={styles.list({ className })}
      {...props}
    />
  );
});
NavigationMenuList.displayName = "NavigationMenuList";

export type NavigationMenuItemProps = Omit<
  BaseNavigationMenuItemProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuItem = React.forwardRef<
  HTMLLIElement,
  NavigationMenuItemProps
>(function NavigationMenuItem({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Item
      ref={ref}
      className={styles.item({ className })}
      {...props}
    />
  );
});
NavigationMenuItem.displayName = "NavigationMenuItem";

export type NavigationMenuLinkProps = Omit<
  BaseNavigationMenuLinkProps,
  "className"
> & {
  className?: string;
  hasPopup?: boolean;
};

export const NavigationMenuLink = React.forwardRef<
  HTMLAnchorElement,
  NavigationMenuLinkProps
>(function NavigationMenuLink(
  { active = false, className, hasPopup = false, ...props },
  ref,
) {
  const styles = navigationMenuVariants({ active, hasPopup });
  return (
    <BaseNavigationMenu.Link
      ref={ref}
      active={active}
      className={styles.link({ className })}
      {...props}
    />
  );
});
NavigationMenuLink.displayName = "NavigationMenuLink";

export type NavigationMenuTriggerProps = Omit<
  BaseNavigationMenuTriggerProps,
  "className"
> & {
  active?: boolean;
  className?: string;
  hasPopup?: boolean;
};

export const NavigationMenuTrigger = React.forwardRef<
  HTMLButtonElement,
  NavigationMenuTriggerProps
>(function NavigationMenuTrigger(
  { active = false, className, hasPopup = true, ...props },
  ref,
) {
  const styles = navigationMenuVariants({ active, hasPopup });
  return (
    <BaseNavigationMenu.Trigger
      ref={ref}
      className={styles.trigger({ className })}
      {...props}
    />
  );
});
NavigationMenuTrigger.displayName = "NavigationMenuTrigger";

export type NavigationMenuTextProps =
  React.HTMLAttributes<HTMLSpanElement> & {
    active?: boolean;
    hasPopup?: boolean;
  };

export const NavigationMenuText = React.forwardRef<
  HTMLSpanElement,
  NavigationMenuTextProps
>(function NavigationMenuText(
  { active = false, className, hasPopup = false, ...props },
  ref,
) {
  const styles = navigationMenuVariants({ active, hasPopup });
  return <span ref={ref} className={styles.text({ className })} {...props} />;
});
NavigationMenuText.displayName = "NavigationMenuText";

export type NavigationMenuContentProps = Omit<
  BaseNavigationMenuContentProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuContent = React.forwardRef<
  HTMLDivElement,
  NavigationMenuContentProps
>(function NavigationMenuContent({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Content
      ref={ref}
      className={styles.content({ className })}
      {...props}
    />
  );
});
NavigationMenuContent.displayName = "NavigationMenuContent";

export type NavigationMenuPopupProps = Omit<
  BaseNavigationMenuPopupProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuPopup = React.forwardRef<
  HTMLElement,
  NavigationMenuPopupProps
>(function NavigationMenuPopup({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Popup
      ref={ref}
      className={styles.popup({ className })}
      {...props}
    />
  );
});
NavigationMenuPopup.displayName = "NavigationMenuPopup";

export type NavigationMenuViewportProps = Omit<
  BaseNavigationMenuViewportProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuViewport = React.forwardRef<
  HTMLDivElement,
  NavigationMenuViewportProps
>(function NavigationMenuViewport({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Viewport
      ref={ref}
      className={styles.viewport({ className })}
      {...props}
    />
  );
});
NavigationMenuViewport.displayName = "NavigationMenuViewport";

export type NavigationMenuIconProps = Omit<
  BaseNavigationMenuIconProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuIcon = React.forwardRef<
  HTMLSpanElement,
  NavigationMenuIconProps
>(function NavigationMenuIcon(
  { className, children = <Glyph name="chevron-down" />, ...props },
  ref,
) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Icon
      ref={ref}
      className={styles.icon({ className })}
      {...props}
    >
      {children}
    </BaseNavigationMenu.Icon>
  );
});
NavigationMenuIcon.displayName = "NavigationMenuIcon";

export type NavigationMenuArrowProps = Omit<
  BaseNavigationMenuArrowProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuArrow = React.forwardRef<
  HTMLDivElement,
  NavigationMenuArrowProps
>(function NavigationMenuArrow({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Arrow
      ref={ref}
      className={styles.arrow({ className })}
      {...props}
    />
  );
});
NavigationMenuArrow.displayName = "NavigationMenuArrow";

export type NavigationMenuBackdropProps = Omit<
  BaseNavigationMenuBackdropProps,
  "className"
> & {
  className?: string;
};

export const NavigationMenuBackdrop = React.forwardRef<
  HTMLDivElement,
  NavigationMenuBackdropProps
>(function NavigationMenuBackdrop({ className, ...props }, ref) {
  const styles = navigationMenuVariants();
  return (
    <BaseNavigationMenu.Backdrop
      ref={ref}
      className={styles.backdrop({ className })}
      {...props}
    />
  );
});
NavigationMenuBackdrop.displayName = "NavigationMenuBackdrop";

export type NavigationMenuPortalProps = BaseNavigationMenuPortalProps;
export type NavigationMenuPositionerProps = BaseNavigationMenuPositionerProps;

export const NavigationMenuPortal = BaseNavigationMenu.Portal;
export const NavigationMenuPositioner = BaseNavigationMenu.Positioner;

export const NavigationMenu = Object.assign(NavigationMenuRoot, {
  Root: NavigationMenuRoot,
  List: NavigationMenuList,
  Item: NavigationMenuItem,
  Link: NavigationMenuLink,
  Trigger: NavigationMenuTrigger,
  Text: NavigationMenuText,
  Content: NavigationMenuContent,
  Popup: NavigationMenuPopup,
  Portal: NavigationMenuPortal,
  Positioner: NavigationMenuPositioner,
  Viewport: NavigationMenuViewport,
  Icon: NavigationMenuIcon,
  Arrow: NavigationMenuArrow,
  Backdrop: NavigationMenuBackdrop,
});
