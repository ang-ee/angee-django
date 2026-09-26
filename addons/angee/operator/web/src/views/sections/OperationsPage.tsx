import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Badge,
  LogStream,
  RowsListView,
  defineRowAction,
  useStatusTone,
  textRoleVariants,
  useConfirm,
  useToast,
  errorMessage,
  type ListColumn,
  type RowActionDeclaration,
} from "@angee/ui";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAuthoredQuery } from "@angee/refine";

import {
  JOB_RUN_PREVIEW_QUERY,
  STACK_BUILD_MUTATION,
  STACK_DESTROY_MUTATION,
  STACK_DOWN_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import { useJobRunOperation } from "../../data/job-run";
import { OPERATOR_PROVIDER } from "../../data/operator-provider";
import type { JobState } from "../../data/types";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { useOperatorRows } from "../parts/operator-rows";
import { useRunDaemonAction } from "../parts/run-action";

/** A stack lifecycle control: its label, tone, variables, and handler. */
interface StackAction {
  field: string;
  label: string;
  variant: "secondary" | "ghost";
  /** Destructive — require a styled confirmation first. */
  dangerous?: boolean;
  perform: () => Promise<boolean>;
}

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
  const statusTone = useStatusTone();
  const t = useOperatorT();
  const { rows, fetching, error, refetch } = useOperatorRows(
    { operations: true },
    (snapshot) => daemonRowsByName(snapshot.jobs),
  );
  const { runJob, runJobAndRestart, stackActions, runStack, busy, operation } = useOperationActions(refetch);
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
      {
        field: "name",
        header: t("operations.column.name"),
        render: (job) => <span className="font-medium text-fg">{job.name}</span>,
      },
      {
        field: "runtime",
        header: t("operations.column.runtime"),
        render: (job) => <span className={textRoleVariants({ role: "meta" })}>{job.runtime}</span>,
      },
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

      <Card>
        <CardHeader>
          <CardTitle>{t("operations.stack.title")}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-2">
            {stackActions.map((action) => (
              <Button
                disabled={busy}
                key={action.field}
                onClick={() => runStack(action)}
                size="sm"
                variant={action.variant}
              >
                {action.label}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>
      {operation ? (
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2">
            <CardTitle>{operation.rootJob}</CardTitle>
            <Badge density="compact" shape="pill" tone={statusTone(operation.status)}>
              {operation.status.toLowerCase()}
            </Badge>
          </CardHeader>
          <CardContent>
            <LogStream lines={[...operation.nodes.map((node) => `${node.kind.toLowerCase()} ${node.name}: ${node.status.toLowerCase()}${node.message ? ` — ${node.message}` : ""}`), ...(operation.output ? operation.output.split("\n") : []), ...(operation.error ? [operation.error] : [])]} />
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
  stackActions: readonly StackAction[];
  runStack: (action: StackAction) => void;
  busy: boolean;
  operation: ReturnType<typeof useJobRunOperation>["operation"];
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();
  const runDaemon = useRunDaemonAction(refetch);
  const jobRun = useJobRunOperation();
  const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(null);
  const previewRequestRef = useRef<PreviewRequest | null>(null);
  const processingPreviewRef = useRef<number | null>(null);
  const nextPreviewIdRef = useRef(0);
  const mountedRef = useRef(true);
  const preview = useAuthoredQuery(
    JOB_RUN_PREVIEW_QUERY,
    { name: previewRequest?.job.name ?? "", chainedRestart: true },
    { dataProviderName: OPERATOR_PROVIDER, enabled: previewRequest !== null },
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

  const stackActions = useMemo<readonly StackAction[]>(
    () => [
      {
        field: "stackBuild",
        label: t("operations.stack.build"),
        variant: "secondary",
        perform: () =>
          runDaemon({
            run: build.run,
            field: "stackBuild",
            variables: {},
            label: t("operations.stack.build"),
          }),
      },
      {
        field: "stackUp",
        label: t("operations.stack.up"),
        variant: "secondary",
        perform: () =>
          runDaemon({
            run: up.run,
            field: "stackUp",
            variables: {},
            label: t("operations.stack.up"),
          }),
      },
      {
        field: "stackDown",
        label: t("operations.stack.down"),
        variant: "ghost",
        perform: () =>
          runDaemon({
            run: down.run,
            field: "stackDown",
            variables: {},
            label: t("operations.stack.down"),
          }),
      },
      {
        field: "stackDestroy",
        label: t("operations.stack.destroy"),
        variant: "ghost",
        dangerous: true,
        perform: () =>
          runDaemon({
            run: destroy.run,
            field: "stackDestroy",
            variables: { purge: false },
            label: t("operations.stack.destroy"),
          }),
      },
    ],
    [build.run, destroy.run, down.run, runDaemon, t, up.run],
  );

  const runStack = useMemo(
    () => (action: StackAction) => {
      void (async () => {
        if (action.dangerous) {
          const ok = await confirm({
            title: t("operations.stack.destroy.confirm.title"),
            body: t("operations.stack.destroy.confirm.body"),
            confirm: action.label,
            danger: true,
          });
          if (!ok) return;
        }
        await action.perform();
      })();
    },
    [confirm, t],
  );

  return { runJob, runJobAndRestart, stackActions, runStack, busy, operation: jobRun.operation };
}
