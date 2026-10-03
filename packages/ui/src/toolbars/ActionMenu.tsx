import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { Button, type ButtonSize, type ButtonVariant } from "../ui/button";
import { DropdownMenu } from "../ui/dropdown-menu";
import { ActionMenuContext } from "../ui/action-menu-context";
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
  extends Omit<React.ButtonHTMLAttributes<HTMLElement>, "children"> {
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
  const { tabIndex, ...restNativeProps } = nativeProps;
  if (menu) {
    return (
      <DropdownMenu.Item
        {...restNativeProps}
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
      type="button"
      variant={variant}
      size="sm"
      disabled={disabled}
      loading={loading}
      loadingText={loadingText}
      tabIndex={tabIndex}
      {...restNativeProps}
    >
      {glyph ? <Glyph decorative name={glyph} /> : null}
      {children}
    </Button>
  );
});
ActionTrigger.displayName = "ActionTrigger";

export interface ActionMenuProps {
  children: React.ReactNode;
  /** Defaults to the shared UI Actions label; domains supply their own copy. */
  label?: React.ReactNode;
  glyph?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  blocked?: boolean;
  loading?: boolean;
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
}: ActionMenuProps): React.ReactElement {
  const t = useUiT();
  const triggerRef = React.useRef<HTMLElement>(null);
  const menu = React.useMemo(() => ({ blocked, finalFocusRef: triggerRef }), [blocked]);
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger render={
        <Button ref={triggerRef} type="button" variant={variant} size={size} loading={loading}>
          <Glyph decorative name={glyph} />
          {label ?? t("list.actions")}
        </Button>
      } />
      <DropdownMenu.Portal keepMounted>
        <DropdownMenu.Positioner sideOffset={6} align="start">
          <DropdownMenu.Content className="w-52">
            <ActionMenuContext.Provider value={menu}>
              {children}
            </ActionMenuContext.Provider>
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
