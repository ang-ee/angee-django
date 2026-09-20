import * as React from "react";

export interface RecordActionMenuContextValue {
  blocked: boolean;
  finalFocusRef: React.RefObject<HTMLElement | null>;
}

export const RecordActionMenuContext =
  React.createContext<RecordActionMenuContextValue | null>(null);

/** Return the toolbar Actions trigger when a dialog opened from its menu. */
export function useRecordActionFinalFocus(): React.RefObject<HTMLElement | null> | undefined {
  return React.useContext(RecordActionMenuContext)?.finalFocusRef;
}
