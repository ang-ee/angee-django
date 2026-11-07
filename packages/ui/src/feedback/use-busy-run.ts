import { useCallback, useState } from "react";

import { useUiT } from "../i18n";
import { errorMessage } from "./error-message";
import { useToast } from "./Toast";

export interface BusyRun {
  /** True while a `run(...)` or `settle(...)` action is in flight. */
  busy: boolean;
  /** Run an async action, tracking `busy` and firing `onChanged` on success; a failure rejects. */
  run: <T>(action: () => Promise<T>) => Promise<T>;
  /** Run an action whose caller acts on its resolved value: a thrown failure toasts once and resolves `fallback`. */
  settle: <T>(action: () => Promise<T>, fallback: T) => Promise<T>;
}

/**
 * Wrap rendered async action handlers so a surface gets one busy flag plus a
 * post-success callback, such as a list or tree refetch. `run` keeps a
 * rejecting contract for a caller that owns its failure (an inline form);
 * `settle` is the one error surface for verbs whose callers act only on the
 * resolved value. Render under `ToastProvider`.
 * @owner ActionTrigger
 */
export function useBusyRun(onChanged?: () => void): BusyRun {
  const [inFlight, setInFlight] = useState(0);
  const toast = useToast();
  const t = useUiT();
  const run = useCallback(
    async <T>(action: () => Promise<T>): Promise<T> => {
      setInFlight((current) => current + 1);
      try {
        const result = await action();
        onChanged?.();
        return result;
      } finally {
        setInFlight((current) => Math.max(0, current - 1));
      }
    },
    [onChanged],
  );
  const settle = useCallback(
    <T>(action: () => Promise<T>, fallback: T): Promise<T> => run(action).catch((error: unknown) => {
      toast.danger({ title: errorMessage(error, t("error.generic")) });
      return fallback;
    }),
    [run, t, toast],
  );
  return { busy: inFlight > 0, run, settle };
}
