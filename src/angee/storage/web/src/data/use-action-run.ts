import { errorMessage, useBusyRun, useToast, type BusyRun } from "@angee/ui";
import { useCallback } from "react";

import { useStorageT } from "../i18n";

export interface StorageActionRun extends BusyRun {
  /** Run a verb that resolves whether it completed; a thrown failure toasts once and resolves false. */
  settle: (action: () => Promise<boolean>) => Promise<boolean>;
}

/**
 * The storage verbs' one error surface. Each verb disables Refine's own
 * notification and settles here, so a caller acts on the resolved boolean and
 * adds no toast or rejection handler of its own. `run` keeps `useBusyRun`'s
 * rejecting contract for verbs whose caller owns their failure (an inline
 * form); `onChanged` fires after each completed run, as `useBusyRun` fires it.
 */
export function useStorageActionRun(onChanged?: () => void): StorageActionRun {
  const { busy, run } = useBusyRun(onChanged);
  const toast = useToast();
  const t = useStorageT();
  const settle = useCallback(
    (action: () => Promise<boolean>) => run(action).catch((error: unknown) => {
      toast.danger({ title: errorMessage(error, t("action.failed")) });
      return false;
    }),
    [run, t, toast],
  );
  return { busy, run, settle };
}
