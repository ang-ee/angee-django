import * as React from "react";
import { Menu as BaseMenu } from "@base-ui/react/menu";
import type {
  MenuCheckboxItemIndicatorProps as BaseMenuCheckboxItemIndicatorProps,
  MenuCheckboxItemProps as BaseMenuCheckboxItemProps,
  MenuGroupLabelProps as BaseMenuGroupLabelProps,
  MenuItemProps as BaseMenuItemProps,
  MenuLinkItemProps as BaseMenuLinkItemProps,
  MenuPopupProps as BaseMenuPopupProps,
  MenuRadioItemIndicatorProps as BaseMenuRadioItemIndicatorProps,
  MenuRadioItemProps as BaseMenuRadioItemProps,
  MenuSubmenuTriggerProps as BaseMenuSubmenuTriggerProps,
} from "@base-ui/react/menu";

import { Glyph } from "../chrome/Glyph";
import { tv, type VariantProps } from "../lib/variants";
import { POPUP_BASE } from "./popover";

// DropdownMenu and ContextMenu are the same styled-part set over Base UI's
// shared Menu parts — Base UI's ContextMenu re-exports Menu's Item/Popup/
// CheckboxItem/etc. as the very same component objects. This module owns the
// shared menu recipe and the styled-part factory; each menu module composes it
// and adds only the parts whose types differ (Root/Trigger/Viewport generics).

// The slots every menu shares. ContextMenu adds a `trigger` slot via the
// recipe factory; nothing else differs between the two recipes.
const MENU_SLOTS = {
  content: `${POPUP_BASE} min-w-44 p-1`,
  item:
    "relative flex h-8 cursor-pointer select-none items-center gap-2 rounded px-2 text-13 text-fg outline-none transition-colors data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 data-[highlighted]:bg-inset [&_.glyph]:size-3.5 [&_.glyph]:shrink-0 [&_.glyph]:text-fg-muted",
  indicator:
    "absolute left-2 flex size-3.5 items-center justify-center text-brand [&_.glyph]:size-3.5",
  radioIndicator:
    "absolute left-2 flex size-3.5 items-center justify-center text-brand before:size-1.5 before:rounded-full before:bg-current before:content-['']",
  label: "px-2 py-1.5 text-2xs font-semibold uppercase text-fg-muted",
  separator: "-mx-1 my-1 h-px bg-border-subtle",
  shortcut: "ml-auto text-2xs text-fg-muted",
  submenuIcon:
    "ml-auto flex size-3.5 items-center justify-center text-fg-muted transition-transform data-[open]:rotate-90 [&_.glyph]:size-3.5",
} as const;

const MENU_VARIANTS = {
  inset: {
    true: { item: "pl-8" },
    false: { item: "" },
  },
  variant: {
    default: { item: "" },
    danger: {
      item: "text-danger-text data-[highlighted]:bg-danger-soft data-[highlighted]:text-danger-text",
    },
  },
} as const;

const MENU_DEFAULTS = {
  inset: false,
  variant: "default",
} as const;

// The concrete base recipe — anchors the recipe and variant-prop types. `tv` is
// a callable object (no applicable type arguments) whose return type is
// invariant on its exact slots literal, so the shared types are taken from this
// value rather than from `tv`'s parameters, and the styled-part factory consumes
// the recipe through the minimal call signature `MenuRecipeFn` (below) instead.
function buildBaseMenuRecipe() {
  return tv({
    slots: MENU_SLOTS,
    variants: MENU_VARIANTS,
    defaultVariants: MENU_DEFAULTS,
  });
}

/** A built menu recipe — the shared slots/variants every menu uses. */
export type MenuRecipe = ReturnType<typeof buildBaseMenuRecipe>;

/** The variant props shared by every menu recipe (`inset` / `variant`). */
export type MenuRecipeProps = VariantProps<MenuRecipe>;

/** The styled item variants a menu exposes (`default` | `danger`). */
export type MenuItemVariant = NonNullable<MenuRecipeProps["variant"]>;

/** A slot class-name builder, as returned for each slot of a built recipe. */
export type MenuSlotFn = ReturnType<MenuRecipe>["item"];

/**
 * The minimal call signature the styled-part factory needs: invoke the recipe
 * (optionally with variant props) and read the shared slot builders. Every tv
 * recipe with at least these slots satisfies it — extra slots are allowed — so
 * `createStyledMenuParts` is decoupled from tv's invariant return type.
 */
export type MenuRecipeFn = (props?: MenuRecipeProps) => {
  content: MenuSlotFn;
  item: MenuSlotFn;
  indicator: MenuSlotFn;
  radioIndicator: MenuSlotFn;
  label: MenuSlotFn;
  separator: MenuSlotFn;
  shortcut: MenuSlotFn;
  submenuIcon: MenuSlotFn;
};

/**
 * A menu recipe whose call result exposes the shared slots plus the given extra
 * slots. A single call signature (returning a superset of the shared slots) so
 * it stays assignable to `MenuRecipeFn` and exposes the extra slots directly.
 */
export type MenuRecipeWithSlots<ExtraSlots extends Record<string, string>> = (
  props?: MenuRecipeProps,
) => ReturnType<MenuRecipeFn> & Record<keyof ExtraSlots, MenuSlotFn>;

/**
 * Build a menu recipe. Pass `extraSlots` to add menu-specific slots (e.g.
 * ContextMenu's styled `trigger`); the shared slots, variants, and defaults are
 * identical for every menu. The returned recipe carries the extra slots in its
 * call result (so the caller can style them) and satisfies `MenuRecipeFn`, the
 * minimal shape the styled-part factory consumes.
 */
export function createMenuRecipe(): MenuRecipeFn;
export function createMenuRecipe<ExtraSlots extends Record<string, string>>(
  extraSlots: ExtraSlots,
): MenuRecipeWithSlots<ExtraSlots>;
export function createMenuRecipe(
  extraSlots?: Record<string, string>,
): MenuRecipeFn {
  return tv({
    slots: { ...MENU_SLOTS, ...extraSlots } as typeof MENU_SLOTS,
    variants: MENU_VARIANTS,
    defaultVariants: MENU_DEFAULTS,
  }) as unknown as MenuRecipeFn;
}

// Shared prop shapes for the styled parts. Base UI's ContextMenu prop types
// are aliases of the Menu prop types, so a single set typed against Menu fits
// both menus; each module re-exports these under its own per-file names.
export type MenuContentProps = Omit<BaseMenuPopupProps, "className"> & {
  className?: string;
};

export type MenuItemProps = Omit<BaseMenuItemProps, "className"> &
  Pick<MenuRecipeProps, "inset" | "variant"> & {
    className?: string;
  };

export type MenuLinkItemProps = Omit<BaseMenuLinkItemProps, "className"> &
  Pick<MenuRecipeProps, "inset" | "variant"> & {
    className?: string;
  };

export type MenuCheckboxItemProps = Omit<
  BaseMenuCheckboxItemProps,
  "className"
> &
  Pick<MenuRecipeProps, "inset" | "variant"> & {
    className?: string;
  };

export type MenuCheckboxItemIndicatorProps = Omit<
  BaseMenuCheckboxItemIndicatorProps,
  "className"
> & {
  className?: string;
};

export type MenuRadioItemProps = Omit<BaseMenuRadioItemProps, "className"> &
  Pick<MenuRecipeProps, "inset" | "variant"> & {
    className?: string;
  };

export type MenuRadioItemIndicatorProps = Omit<
  BaseMenuRadioItemIndicatorProps,
  "className"
> & {
  className?: string;
};

export type MenuSubmenuTriggerProps = Omit<
  BaseMenuSubmenuTriggerProps,
  "className" | "children"
> &
  Pick<MenuRecipeProps, "inset" | "variant"> & {
    children?: React.ReactNode;
    className?: string;
    icon?: React.ReactNode;
  };

export type MenuLabelProps = Omit<BaseMenuGroupLabelProps, "className"> & {
  className?: string;
};

export type MenuSeparatorProps = Omit<
  React.ComponentPropsWithoutRef<typeof BaseMenu.Separator>,
  "className"
> & {
  className?: string;
};

export type MenuShortcutProps = React.HTMLAttributes<HTMLSpanElement> & {
  className?: string;
};

/** The common styled parts shared by DropdownMenu and ContextMenu. */
export interface StyledMenuParts {
  Content: React.ForwardRefExoticComponent<
    MenuContentProps & React.RefAttributes<HTMLDivElement>
  >;
  Item: React.ForwardRefExoticComponent<
    MenuItemProps & React.RefAttributes<HTMLElement>
  >;
  LinkItem: React.ForwardRefExoticComponent<
    MenuLinkItemProps & React.RefAttributes<Element>
  >;
  CheckboxItem: React.ForwardRefExoticComponent<
    MenuCheckboxItemProps & React.RefAttributes<HTMLElement>
  >;
  CheckboxItemIndicator: React.ForwardRefExoticComponent<
    MenuCheckboxItemIndicatorProps & React.RefAttributes<HTMLSpanElement>
  >;
  RadioItem: React.ForwardRefExoticComponent<
    MenuRadioItemProps & React.RefAttributes<HTMLElement>
  >;
  RadioItemIndicator: React.ForwardRefExoticComponent<
    MenuRadioItemIndicatorProps & React.RefAttributes<HTMLSpanElement>
  >;
  SubmenuTrigger: React.ForwardRefExoticComponent<
    MenuSubmenuTriggerProps & React.RefAttributes<HTMLElement>
  >;
  Label: React.ForwardRefExoticComponent<
    MenuLabelProps & React.RefAttributes<HTMLDivElement>
  >;
  Separator: React.ForwardRefExoticComponent<
    MenuSeparatorProps & React.RefAttributes<HTMLDivElement>
  >;
  Shortcut: React.ForwardRefExoticComponent<
    MenuShortcutProps & React.RefAttributes<HTMLSpanElement>
  >;
}

/**
 * Build the styled menu parts that are identical across DropdownMenu and
 * ContextMenu. `recipe` supplies the slot classes; `namePrefix` (e.g.
 * `"DropdownMenu"`) labels the components' `displayName`. Built over Base UI's
 * Menu parts — the very same component objects ContextMenu re-exports — so the
 * result renders correctly under either menu root.
 */
export function createStyledMenuParts(
  recipe: MenuRecipeFn,
  namePrefix: string,
): StyledMenuParts {
  const Content = React.forwardRef<HTMLDivElement, MenuContentProps>(
    function MenuContent({ className, ...props }, ref) {
      const styles = recipe();
      return (
        <BaseMenu.Popup
          ref={ref}
          className={styles.content({ className })}
          {...props}
        />
      );
    },
  );
  Content.displayName = `${namePrefix}Content`;

  const Item = React.forwardRef<HTMLElement, MenuItemProps>(function MenuItem(
    { className, inset = false, variant = "default", ...props },
    ref,
  ) {
    const styles = recipe({ inset, variant });
    return (
      <BaseMenu.Item
        ref={ref}
        className={styles.item({ className })}
        {...props}
      />
    );
  });
  Item.displayName = `${namePrefix}Item`;

  const LinkItem = React.forwardRef<Element, MenuLinkItemProps>(
    function MenuLinkItem(
      { className, inset = false, variant = "default", ...props },
      ref,
    ) {
      const styles = recipe({ inset, variant });
      return (
        <BaseMenu.LinkItem
          ref={ref}
          className={styles.item({ className })}
          {...props}
        />
      );
    },
  );
  LinkItem.displayName = `${namePrefix}LinkItem`;

  const CheckboxItem = React.forwardRef<HTMLElement, MenuCheckboxItemProps>(
    function MenuCheckboxItem(
      { className, inset = true, variant = "default", ...props },
      ref,
    ) {
      const styles = recipe({ inset, variant });
      return (
        <BaseMenu.CheckboxItem
          ref={ref}
          className={styles.item({ className })}
          {...props}
        />
      );
    },
  );
  CheckboxItem.displayName = `${namePrefix}CheckboxItem`;

  const CheckboxItemIndicator = React.forwardRef<
    HTMLSpanElement,
    MenuCheckboxItemIndicatorProps
  >(function MenuCheckboxItemIndicator(
    { className, children = <Glyph name="check" />, ...props },
    ref,
  ) {
    const styles = recipe();
    return (
      <BaseMenu.CheckboxItemIndicator
        ref={ref}
        className={styles.indicator({ className })}
        {...props}
      >
        {children}
      </BaseMenu.CheckboxItemIndicator>
    );
  });
  CheckboxItemIndicator.displayName = `${namePrefix}CheckboxItemIndicator`;

  const RadioItem = React.forwardRef<HTMLElement, MenuRadioItemProps>(
    function MenuRadioItem(
      { className, inset = true, variant = "default", ...props },
      ref,
    ) {
      const styles = recipe({ inset, variant });
      return (
        <BaseMenu.RadioItem
          ref={ref}
          className={styles.item({ className })}
          {...props}
        />
      );
    },
  );
  RadioItem.displayName = `${namePrefix}RadioItem`;

  const RadioItemIndicator = React.forwardRef<
    HTMLSpanElement,
    MenuRadioItemIndicatorProps
  >(function MenuRadioItemIndicator({ className, children, ...props }, ref) {
    const styles = recipe();
    return (
      <BaseMenu.RadioItemIndicator
        ref={ref}
        className={styles.radioIndicator({ className })}
        {...props}
      >
        {children}
      </BaseMenu.RadioItemIndicator>
    );
  });
  RadioItemIndicator.displayName = `${namePrefix}RadioItemIndicator`;

  const SubmenuTrigger = React.forwardRef<HTMLElement, MenuSubmenuTriggerProps>(
    function MenuSubmenuTrigger(
      {
        children,
        className,
        icon = <Glyph name="chevron-right" />,
        inset = false,
        variant = "default",
        ...props
      },
      ref,
    ) {
      const styles = recipe({ inset, variant });
      return (
        <BaseMenu.SubmenuTrigger
          ref={ref}
          className={styles.item({ className })}
          {...props}
        >
          {children}
          <span className={styles.submenuIcon()}>{icon}</span>
        </BaseMenu.SubmenuTrigger>
      );
    },
  );
  SubmenuTrigger.displayName = `${namePrefix}SubmenuTrigger`;

  const Label = React.forwardRef<HTMLDivElement, MenuLabelProps>(
    function MenuLabel({ className, ...props }, ref) {
      const styles = recipe();
      return (
        <BaseMenu.GroupLabel
          ref={ref}
          className={styles.label({ className })}
          {...props}
        />
      );
    },
  );
  Label.displayName = `${namePrefix}Label`;

  const Separator = React.forwardRef<HTMLDivElement, MenuSeparatorProps>(
    function MenuSeparator({ className, ...props }, ref) {
      const styles = recipe();
      return (
        <BaseMenu.Separator
          ref={ref}
          className={styles.separator({ className })}
          {...props}
        />
      );
    },
  );
  Separator.displayName = `${namePrefix}Separator`;

  const Shortcut = React.forwardRef<HTMLSpanElement, MenuShortcutProps>(
    function MenuShortcut({ className, ...props }, ref) {
      const styles = recipe();
      return (
        <span ref={ref} className={styles.shortcut({ className })} {...props} />
      );
    },
  );
  Shortcut.displayName = `${namePrefix}Shortcut`;

  return {
    Content,
    Item,
    LinkItem,
    CheckboxItem,
    CheckboxItemIndicator,
    RadioItem,
    RadioItemIndicator,
    SubmenuTrigger,
    Label,
    Separator,
    Shortcut,
  };
}
