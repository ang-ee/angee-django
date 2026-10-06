import { useCallback } from "react";
import { errorMessage, useToast } from "@angee/ui";

import {
  JOB_RUN_MUTATION,
  LATEST_JOB_RUN_QUERY,
} from "./documents.daemon";
import { useOperatorAction, useOperatorQuery } from "./transport";
import { useOperatorT } from "../i18n";

const POLL_MS = 2_000;

/** One durable daemon-owned job run, shared by every restart entry point. */
export function useJobRunOperation(options: { enabled?: boolean } = {}) {
  const toast = useToast();
  const t = useOperatorT();
  const enabled = options.enabled ?? true;
  const latest = useOperatorQuery(LATEST_JOB_RUN_QUERY, undefined, {
    enabled,
    // Keep observing while idle too: another browser or the CLI can start a run.
    refetchInterval: enabled ? POLL_MS : false,
  });
  const mutation = useOperatorAction(JOB_RUN_MUTATION);
  const operation = latest.data?.latestJobRun ?? null;
  const active = operation?.status === "PENDING" || operation?.status === "RUNNING";
  const run = useCallback(async (name: string, chainedRestart: boolean) => {
    try {
      const data = await mutation.run({ name, chainedRestart });
      await latest.refetch();
      return data?.jobRun ?? null;
    } catch (error) {
      toast.danger({ title: errorMessage(error, t("operations.startFailed")) });
      return null;
    }
  }, [latest.refetch, mutation.run, t, toast]);

  return {
    operation,
    run,
    refetch: latest.refetch,
    active,
    starting: mutation.result.fetching,
    refreshing: latest.isFetching,
    startError: mutation.result.error,
    queryError: latest.error,
    error: mutation.result.error ?? latest.error,
  };
}
