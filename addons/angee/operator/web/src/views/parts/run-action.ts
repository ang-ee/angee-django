import { errorMessage } from "@angee/sdk";

/** A daemon mutation payload keyed by its single root field (shape varies by op). */
export type DaemonActionData = Record<string, unknown>;

export interface RunDaemonActionParams<V extends Record<string, unknown>> {
  run: (variables: V) => Promise<DaemonActionData>;
  /** The mutation's root field whose presence confirms the action ran. */
  field: string;
  variables: V;
  label: string;
  setError: (message: string | null) => void;
  refetch: () => void;
}

/**
 * Run a daemon mutation safely for a section pane: clear the prior error, run,
 * and surface a failure. The daemon reports failure as a GraphQL error (the
 * transport rejects), not an `ok:false` payload — so any returned root field is
 * a success, and a missing root field counts as failure rather than silent
 * success. Always refetches the snapshot; never leaves an unhandled rejection,
 * so a click handler can `void` it.
 */
export async function runDaemonAction<V extends Record<string, unknown>>(
  params: RunDaemonActionParams<V>,
): Promise<boolean> {
  const { run, field, variables, label, setError, refetch } = params;
  setError(null);
  let succeeded = true;
  try {
    const data = await run(variables);
    if (data[field] == null) {
      succeeded = false;
      setError(`${label} returned no result.`);
    }
  } catch (error) {
    succeeded = false;
    setError(errorMessage(error, `${label} failed.`));
  } finally {
    refetch();
  }
  return succeeded;
}
