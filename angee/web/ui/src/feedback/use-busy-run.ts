import { useCallback, useState } from "react";

export interface BusyRun {
  /** True while a `run(...)` action is in flight. */
  busy: boolean;
  /** Run an async action, tracking `busy` and firing `onChanged` on success. */
  run: <T>(action: () => Promise<T>) => Promise<T>;
}

/**
 * Wrap rendered async action handlers so a surface gets one busy flag plus a
 * post-success callback, such as a list or tree refetch.
 */
export function useBusyRun(onChanged?: () => void): BusyRun {
  const [inFlight, setInFlight] = useState(0);
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
  return { busy: inFlight > 0, run };
}
