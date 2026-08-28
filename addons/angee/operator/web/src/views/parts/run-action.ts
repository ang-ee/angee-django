import { useCallback } from "react";

import { errorMessage, useToast, type ToastApi } from "@angee/ui";

/** A daemon mutation payload keyed by its single root field (shape varies by op). */
export type DaemonActionData = Record<string, unknown>;

export type RunDaemonActionParams<
  Data extends object,
  V extends Record<string, unknown>,
> = {
  run: (variables: V) => Promise<Data | undefined>;
  /** The mutation's root field whose presence confirms the action ran. */
  field: string;
  variables: V;
  label: string;
  refetch: () => void;
  toast: Pick<ToastApi, "danger">;
};

type BoundRunDaemonAction = <Data extends object, V extends Record<string, unknown>>(
  params: Omit<RunDaemonActionParams<Data, V>, "refetch" | "toast">,
) => Promise<boolean>;

/** Bind daemon action failures to the console toast queue and snapshot refresh. */
export function useRunDaemonAction(refetch: () => void): BoundRunDaemonAction {
  const toast = useToast();
  return useCallback<BoundRunDaemonAction>(
    (params) => runDaemonAction({ ...params, refetch, toast }),
    [refetch, toast],
  );
}

/**
 * Run a daemon mutation safely for a section pane: run, surface failures as
 * toasts, and refetch. The daemon reports failure as a GraphQL error (the
 * transport rejects), not an `ok:false` payload — so any returned root field
 * counts as success, and a missing root field (or no payload at all) counts as
 * failure rather than silent success. Never leaves an unhandled rejection, so a
 * click handler can `void` it.
 */
export async function runDaemonAction<
  Data extends object,
  V extends Record<string, unknown>,
>(
  params: RunDaemonActionParams<Data, V>,
): Promise<boolean> {
  const { run, field, variables, label, refetch, toast } = params;
  let succeeded = true;
  try {
    const data = await run(variables);
    if (data == null || (data as Record<string, unknown>)[field] == null) {
      succeeded = false;
      toast.danger({ title: `${label} returned no result.` });
    }
  } catch (error) {
    succeeded = false;
    toast.danger({ title: errorMessage(error, `${label} failed.`) });
  } finally {
    refetch();
  }
  return succeeded;
}
