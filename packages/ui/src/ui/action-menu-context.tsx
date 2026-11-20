import * as React from "react";

export interface ActionMenuContextValue {
  blocked: boolean;
  /** Register one pending contribution until it settles or unmounts. */
  registerPending: () => () => void;
  /** Register one rendered menu item until it unmounts; a menu holding none hides its trigger. */
  registerItem: () => () => void;
}

export const ActionMenuContext =
  React.createContext<ActionMenuContextValue | null>(null);

/**
 * Count this rendered item toward the enclosing `ActionMenu`, so a menu whose
 * contributions all render nothing shows no trigger. Outside one it does nothing.
 */
export function useActionMenuItem(): void {
  const registerItem = React.useContext(ActionMenuContext)?.registerItem;
  React.useLayoutEffect(() => registerItem?.(), [registerItem]);
}
