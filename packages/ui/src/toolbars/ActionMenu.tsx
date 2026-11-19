import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { Button, type ButtonSize, type ButtonVariant } from "../ui/button";
import { DropdownMenu, type DropdownMenuPositionerProps } from "../ui/dropdown-menu";
import { ActionMenuContext } from "../ui/action-menu-context";
import { DialogReturnFocusContext } from "../ui/dialog";
import { useRecordChromeContextMaybe } from "../views/resource/record-chrome-context";
import { useRuntimeViewAs } from "../runtime";
import { useUiT } from "../i18n";

/**
 * Render an action as a toolbar button or a native item inside ActionMenu.
 * Dialog owners may use it as their trigger; the menu keeps contributions
 * mounted after closing and supplies the toolbar trigger for focus restoration.
 * Ambient record dirty/pending and view-as gates apply on either surface.
 */
export interface ActionTriggerProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children" | "type"> {
  children: React.ReactNode;
  disabled?: boolean;
  glyph?: string;
  loading?: boolean;
  loadingText?: React.ReactNode;
  variant?: ButtonVariant;
}

/**
 * @kind primitive
 */
export const ActionTrigger = React.forwardRef<
  HTMLButtonElement,
  ActionTriggerProps
>(function ActionTrigger({
  children,
  disabled: declaredDisabled = false,
  glyph,
  loading = false,
  loadingText,
  variant = "secondary",
  ...nativeProps
}, ref): React.ReactElement {
  const preview = useRuntimeViewAs();
  const chrome = useRecordChromeContextMaybe();
  const disabled = declaredDisabled || Boolean(chrome?.actionsBlocked || preview.viewAs || preview.pending);
  const menu = React.useContext(ActionMenuContext);
  const registerPending = menu?.registerPending;
  React.useEffect(() => {
    if (loading && registerPending) return registerPending();
  }, [loading, registerPending]);
  const { tabIndex, ...restNativeProps } = nativeProps;
  if (menu) {
    return (
      <DropdownMenu.Item
        // Base UI types Item for its default div; this owner always renders a button.
        {...restNativeProps as React.HTMLAttributes<HTMLElement>}
        render={<button ref={ref} type="button" />}
        nativeButton
        variant={variant === "danger" ? "danger" : "default"}
        disabled={menu.blocked || disabled || loading}
      >
        {glyph ? <Glyph decorative name={glyph} /> : null}
        {children}
      </DropdownMenu.Item>
    );
  }
  return (
    <Button
      ref={ref}
      variant={variant}
      size="sm"
      disabled={disabled}
      loading={loading}
      loadingText={loadingText}
      tabIndex={tabIndex}
      {...restNativeProps}
      type="button"
    >
      {glyph ? <Glyph decorative name={glyph} /> : null}
      {children}
    </Button>
  );
});
ActionTrigger.displayName = "ActionTrigger";

export interface ActionMenuProps {
  children: React.ReactNode;
  /** Defaults to Actions; pass null with an aria-label for an icon-only trigger. */
  label?: React.ReactNode;
  /** The trigger's glyph (`more-vertical` by default); `null` for a label-only trigger. */
  glyph?: string | null;
  variant?: ButtonVariant;
  size?: ButtonSize;
  blocked?: boolean;
  loading?: boolean;
  disabled?: boolean;
  /** A count on the trigger (a pane menu's unread). */
  badge?: number;
  align?: DropdownMenuPositionerProps["align"];
  "aria-label"?: string;
  className?: string;
}

/** The glyphs that mean "more": a menu behind one of them is its group's overflow menu. */
const OVERFLOW_GLYPHS: ReadonlySet<string> = new Set(["more-vertical", "more-horizontal"]);

/** A toolbar menu whose contributed dialogs survive item activation and closing.
 * Its content stays mounted while closed, so it knows whether any item rendered:
 * a menu whose contributions all render nothing shows no trigger.
 *
 * An overflow menu (the `more-vertical` default or `more-horizontal`, labeled or
 * not) is the last control of its group and never sits between two buttons: its
 * trigger orders itself after every sibling of its row (`order-last`,
 * `data-overflow-trigger`), whichever owner, contribution or portal rendered them
 * and in whatever order. No caller positions it. A menu behind any other glyph
 * (New, Connect, a column picker) is a button of its own and stays where it is
 * written.
 * @kind primitive
 */
export function ActionMenu({
  children,
  label,
  glyph = "more-vertical",
  variant = "ghost",
  size = "md",
  blocked = false,
  loading = false,
  disabled = false,
  badge,
  align = "start",
  "aria-label": ariaLabel,
  className,
}: ActionMenuProps): React.ReactElement {
  const t = useUiT();
  const overflow = glyph !== null && OVERFLOW_GLYPHS.has(glyph);
  const triggerRef = React.useRef<HTMLElement>(null);
  const [pendingCount, setPendingCount] = React.useState(0);
  const registerPending = React.useCallback(() => {
    setPendingCount((count) => count + 1);
    return () => setPendingCount((count) => count - 1);
  }, []);
  const [itemCount, setItemCount] = React.useState(0);
  const registerItem = React.useCallback(() => {
    setItemCount((count) => count + 1);
    return () => setItemCount((count) => count - 1);
  }, []);
  const menu = React.useMemo(
    () => ({ blocked, registerPending, registerItem }),
    [blocked, registerPending, registerItem],
  );
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger render={
        <Button ref={triggerRef} type="button" variant={variant} size={size} disabled={disabled} badge={badge}
          className={cn(overflow && "order-last", className)} aria-label={ariaLabel} loading={loading || pendingCount > 0}
          hidden={itemCount === 0} data-overflow-trigger={overflow ? "" : undefined}>
          {glyph ? <Glyph decorative name={glyph} /> : null}
          {label === undefined ? t("list.actions") : label}
        </Button>
      } />
      <DropdownMenu.Portal keepMounted>
        <DropdownMenu.Positioner sideOffset={6} align={align}>
          <DropdownMenu.Content className="w-52">
            <ActionMenuContext.Provider value={menu}>
              <DialogReturnFocusContext.Provider value={triggerRef}>
                {children}
              </DialogReturnFocusContext.Provider>
            </ActionMenuContext.Provider>
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
