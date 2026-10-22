import { Card, CardContent, CardHeader, CardTitle, DetailSection, LogStream, RecordActionBar, RowsListView, StatusBadge, defineRowAction, statusLabel, useConfirm, useToast, errorMessage, type ActionDescriptor, type ListColumn, type RowActionDeclaration } from "@angee/ui";
import type { Row } from "@angee/metadata";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  JOB_RUN_PREVIEW_QUERY,
  STACK_BUILD_MUTATION,
  STACK_DESTROY_MUTATION,
  STACK_DOWN_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorQuery } from "../../data/transport";
import { useJobRunOperation } from "../../data/job-run";
import type { JobState } from "../../data/types";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { useOperatorRows } from "../parts/operator-rows";
import { useDaemonMutationRun } from "../parts/daemon-verbs";

type JobRowData = DaemonRow<JobState>;

interface PreviewRequest {
  id: number;
  job: JobState;
  promise: Promise<void>;
  resolve: () => void;
  resolved: boolean;
}

/** Operations page: the daemon job list with run + stack lifecycle controls. */
export function OperationsPage(): ReactNode {
  const t = useOperatorT();
  const { rows, fetching, error, refetch } = useOperatorRows(
    { operations: true },
    (snapshot) => daemonRowsByName(snapshot.jobs),
  );
  const { runJob, runJobAndRestart, stackActions, busy, operation } = useOperationActions(refetch);
  const rowActions = useMemo<readonly RowActionDeclaration<JobRowData>[]>(
    () => [
      defineRowAction({
        kind: "page",
        id: "run-job",
        label: t("operations.run"),
        variant: "secondary",
        disabled: () => busy,
        pendingPolicy: "active-row",
        onSelect: runJob,
      }),
      defineRowAction({
        kind: "page",
        id: "run-job-and-restart",
        label: t("operations.runAndRestart"),
        variant: "ghost",
        disabled: () => busy,
        pendingPolicy: "active-row",
        onSelect: runJobAndRestart,
      }),
    ],
    [busy, runJob, runJobAndRestart, t],
  );

  const columns = useMemo<readonly ListColumn<JobRowData>[]>(
    () => [
      { field: "name", header: t("operations.column.name") },
      { field: "runtime", header: t("operations.column.runtime") },
    ],
    [t],
  );

  return (
    <>
      <RowsListView<JobRowData>
        rows={rows}
        columns={columns}
        rowActions={rowActions}
        fetching={fetching}
        error={error}
        emptyContent={t("operations.empty")}
      />

      {/* The stack's lifecycle verbs: the shared verb bar, confirming the destructive one. */}
      <DetailSection title={t("operations.stack.title")}>
        <RecordActionBar record={{} as Row} actions={stackActions} blocked={busy} menuPlacement="leading" />
      </DetailSection>
      {operation ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle>{operation.rootJob}</CardTitle>
            <StatusBadge value={operation.status} />
          </CardHeader>
          <CardContent>
            <LogStream lines={[
              ...operation.nodes.map((node) => `${statusLabel(node.kind)} ${node.name}: ${statusLabel(node.status)}${node.message ? ` — ${node.message}` : ""}`),
              ...(operation.output ? operation.output.split("\n") : []),
              ...(operation.error ? [operation.error] : []),
            ]} />
          </CardContent>
        </Card>
      ) : null}
    </>
  );
}

/** Operations actions: per-job run plus stack lifecycle controls. */
function useOperationActions(refetch: () => void): {
  runJob: (job: JobState) => Promise<void>;
  runJobAndRestart: (job: JobState) => Promise<void>;
  stackActions: readonly ActionDescriptor[];
  busy: boolean;
  operation: ReturnType<typeof useJobRunOperation>["operation"];
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();
  const runDaemon = useDaemonMutationRun(refetch);
  const jobRun = useJobRunOperation();
  const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(null);
  const previewRequestRef = useRef<PreviewRequest | null>(null);
  const processingPreviewRef = useRef<number | null>(null);
  const nextPreviewIdRef = useRef(0);
  const mountedRef = useRef(true);
  const preview = useOperatorQuery(
    JOB_RUN_PREVIEW_QUERY,
    { name: previewRequest?.job.name ?? "", chainedRestart: true },
    { enabled: previewRequest !== null },
  );

  const build = useOperatorAction(STACK_BUILD_MUTATION);
  const up = useOperatorAction(STACK_UP_MUTATION);
  const down = useOperatorAction(STACK_DOWN_MUTATION);
  const destroy = useOperatorAction(STACK_DESTROY_MUTATION);
  const busy =
    build.result.fetching ||
    up.result.fetching ||
    down.result.fetching ||
    destroy.result.fetching ||
    jobRun.active || jobRun.starting || preview.isFetching || previewRequest !== null;

  const runJob = useMemo(
    () => async (job: JobState): Promise<void> => {
      await jobRun.run(job.name, false);
      refetch();
    },
    [jobRun.run, refetch],
  );

  const runJobAndRestart = useMemo(
    () => (job: JobState): Promise<void> => {
      const activeRequest = previewRequestRef.current;
      if (activeRequest) return activeRequest.promise;

      let settle!: () => void;
      const promise = new Promise<void>((resolve) => {
        settle = resolve;
      });
      const request: PreviewRequest = {
        id: ++nextPreviewIdRef.current,
        job,
        promise,
        resolve: settle,
        resolved: false,
      };
      previewRequestRef.current = request;
      setPreviewRequest(request);
      return promise;
    },
    [],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const request = previewRequestRef.current;
      if (request && !request.resolved) {
        request.resolved = true;
        request.resolve();
      }
      previewRequestRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!previewRequest || preview.isFetching) return;

    const finish = () => {
      if (previewRequestRef.current?.id === previewRequest.id) {
        previewRequestRef.current = null;
        if (mountedRef.current) setPreviewRequest(null);
      }
      processingPreviewRef.current = null;
      if (!previewRequest.resolved) {
        previewRequest.resolved = true;
        previewRequest.resolve();
      }
    };

    if (preview.error) {
      toast.danger({ title: errorMessage(preview.error, t("operations.previewFailed")) });
      finish();
      return;
    }
    const plan = preview.data?.jobRunPreview;
    if (!plan) {
      toast.danger({ title: t("operations.previewFailed") });
      finish();
      return;
    }
    if (processingPreviewRef.current === previewRequest.id) return;
    processingPreviewRef.current = previewRequest.id;
    void (async () => {
      try {
        const affected = [...plan.jobs, ...plan.services];
        const ok = await confirm({
          title: t("operations.restart.confirm.title"),
          body: t("operations.restart.confirm.body", { affected: affected.join(", ") }),
          confirm: t("operations.runAndRestart"),
        });
        if (!ok || !mountedRef.current) return;
        await jobRun.run(previewRequest.job.name, true);
        refetch();
      } finally {
        finish();
      }
    })();
  }, [confirm, jobRun.run, preview.data, preview.error, preview.isFetching, previewRequest, refetch, t, toast]);

  const stackActions = useMemo<readonly ActionDescriptor[]>(
    () => [
      { id: "stack-build", label: t("operations.stack.build"), placement: "toolbar", primary: true,
        run: () => runDaemon({ run: build.run, field: "stackBuild", variables: {} }) },
      { id: "stack-up", label: t("operations.stack.up"), placement: "toolbar",
        run: () => runDaemon({ run: up.run, field: "stackUp", variables: {} }) },
      { id: "stack-down", label: t("operations.stack.down"), placement: "menu",
        run: () => runDaemon({ run: down.run, field: "stackDown", variables: {} }) },
      { id: "stack-destroy", label: t("operations.stack.destroy"), placement: "menu", danger: true,
        confirm: { title: t("operations.stack.destroy.confirm.title"), body: t("operations.stack.destroy.confirm.body"), danger: true },
        run: () => runDaemon({ run: destroy.run, field: "stackDestroy", variables: { purge: false } }) },
    ],
    [build.run, destroy.run, down.run, runDaemon, t, up.run],
  );

  return { runJob, runJobAndRestart, stackActions, busy, operation: jobRun.operation };
}
