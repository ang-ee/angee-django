import * as React from "react";

export interface ActionMenuContextValue {
  blocked: boolean;
  finalFocusRef: React.RefObject<HTMLElement | null>;
}

export const ActionMenuContext =
  React.createContext<ActionMenuContextValue | null>(null);

/** Return the toolbar trigger when a dialog opened from its menu. */
export function useActionMenuFinalFocus(): React.RefObject<HTMLElement | null> | undefined {
  return React.useContext(ActionMenuContext)?.finalFocusRef;
}
