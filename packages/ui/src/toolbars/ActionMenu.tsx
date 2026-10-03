import * as React from "react";

import { Glyph } from "../chrome/Glyph";
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
  glyph?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  blocked?: boolean;
  loading?: boolean;
  align?: DropdownMenuPositionerProps["align"];
  "aria-label"?: string;
  className?: string;
}

/** A toolbar menu whose contributed dialogs survive item activation and closing. */
export function ActionMenu({
  children,
  label,
  glyph = "more-vertical",
  variant = "ghost",
  size = "md",
  blocked = false,
  loading = false,
  align = "start",
  "aria-label": ariaLabel,
  className,
}: ActionMenuProps): React.ReactElement {
  const t = useUiT();
  const triggerRef = React.useRef<HTMLElement>(null);
  const [pendingCount, setPendingCount] = React.useState(0);
  const registerPending = React.useCallback(() => {
    setPendingCount((count) => count + 1);
    return () => setPendingCount((count) => count - 1);
  }, []);
  const menu = React.useMemo(() => ({ blocked, registerPending }), [blocked, registerPending]);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger render={
        <Button ref={triggerRef} type="button" variant={variant} size={size}
          className={className} aria-label={ariaLabel} loading={loading || pendingCount > 0}>
          <Glyph decorative name={glyph} />
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
