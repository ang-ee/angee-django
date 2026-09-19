import * as React from "react";

import { Glyph } from "../../chrome/Glyph";
import { Button, type ButtonVariant } from "../../ui/button";
import { DropdownMenu } from "../../ui/dropdown-menu";
import { RecordActionMenuContext } from "../../ui/record-action-context";

/** Bind contributed record verbs to the existing record Actions menu. */
export function RecordActionMenuItems({
  blocked,
  children,
  finalFocusRef,
}: {
  blocked: boolean;
  children: React.ReactNode;
  finalFocusRef: React.RefObject<HTMLElement | null>;
}): React.ReactElement {
  const value = React.useMemo(
    () => ({ blocked, finalFocusRef }),
    [blocked, finalFocusRef],
  );
  return (
    <RecordActionMenuContext.Provider value={value}>
      {children}
    </RecordActionMenuContext.Provider>
  );
}

/**
 * Render one record verb in the surface selected by its contribution owner.
 *
 * Outside the Actions menu this is the ordinary toolbar button. Inside the
 * menu it is a native menu item, with the form's dirty/pending gate inherited
 * from RecordActionBar. Dialog owners may use this element as their trigger;
 * the bar keeps its portal mounted while the popup is closed.
 */
export interface RecordActionTriggerProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  children: React.ReactNode;
  disabled?: boolean;
  glyph?: string;
  loading?: boolean;
  variant?: ButtonVariant;
}

export const RecordActionTrigger = React.forwardRef<
  HTMLButtonElement,
  RecordActionTriggerProps
>(function RecordActionTrigger({
  children,
  disabled = false,
  glyph,
  loading = false,
  variant = "secondary",
  ...nativeProps
}, ref): React.ReactElement {
  const menu = React.useContext(RecordActionMenuContext);
  const { tabIndex, ...restNativeProps } = nativeProps;
  if (menu) {
    return (
      <DropdownMenu.Item
        render={<button ref={ref} type="button" {...restNativeProps} />}
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
      tabIndex={tabIndex}
      {...restNativeProps}
    >
      {glyph ? <Glyph decorative name={glyph} /> : null}
      {children}
    </Button>
  );
});
RecordActionTrigger.displayName = "RecordActionTrigger";
