import * as React from "react";

export interface ActionMenuContextValue {
  blocked: boolean;
  /** Register one pending contribution until it settles or unmounts. */
  registerPending: () => () => void;
}

export const ActionMenuContext =
  React.createContext<ActionMenuContextValue | null>(null);
