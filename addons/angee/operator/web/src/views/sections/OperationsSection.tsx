import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  RowsListView,
  useConfirm,
  useToast,
  type ListColumn,
} from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  JOB_RUN_MUTATION,
  STACK_BUILD_MUTATION,
  STACK_DESTROY_MUTATION,
  STACK_DOWN_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { JobState } from "../../data/types";
import { runDaemonAction } from "../parts/run-action";

/** A stack lifecycle control: its label, tone, variables, and handler. */
interface StackAction {
  field: string;
  label: string;
  variant: "secondary" | "ghost";
  /** Destructive — require a styled confirmation first. */
  dangerous?: boolean;
  perform: () => Promise<boolean>;
}

// RowsListView keys rows by `id`; the daemon identifies a job by name.
type JobRowData = JobState & { id: string };

/** Operations pane: the daemon job list with run + stack lifecycle controls. */
export function OperationsSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ operations: true });
  const { runJob, stackActions, runStack, busy } = useOperationActions(refetch);

  const rows = useMemo<readonly JobRowData[]>(
    () => (snapshot?.jobs ?? []).map((job) => ({ ...job, id: job.name })),
    [snapshot],
  );

  const columns = useMemo<readonly ListColumn<JobRowData>[]>(
    () => [
      {
        field: "name",
        header: t("operator.operations.column.name"),
        render: (job) => <span className="font-medium text-fg">{job.name}</span>,
      },
      {
        field: "runtime",
        header: t("operator.operations.column.runtime"),
        render: (job) => <span className="text-13 text-fg-muted">{job.runtime}</span>,
      },
      {
        field: "actions",
        header: t("operator.table.actions"),
        sortable: false,
        align: "right",
        render: (job) => (
          <div className="flex justify-end gap-1">
            <Button
              disabled={busy}
              onClick={() => runJob(job)}
              size="sm"
              variant="secondary"
            >
              {t("operator.operations.run")}
            </Button>
          </div>
        ),
      },
    ],
    [busy, runJob, t],
  );

  return (
    <>
      <RowsListView<JobRowData>
        rows={rows}
        columns={columns}
        fetching={result.fetching}
        error={snapshot ? null : result.error}
        emptyMessage={t("operator.operations.empty")}
      />

      <Card>
        <CardHeader>
          <CardTitle>{t("operator.operations.stack.title")}</CardTitle>
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
    </>
  );
}

/**
 * The operations actions: the per-job run plus the four stack lifecycle controls.
 * Each runs via {@link runDaemonAction} and surfaces a failure as a toast — the
 * live snapshot then reflects the new state, so the pane needs no local result
 * store. The destroy control confirms (styled) before running.
 */
function useOperationActions(refetch: () => void): {
  runJob: (job: JobState) => void;
  stackActions: readonly StackAction[];
  runStack: (action: StackAction) => void;
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();

  const build = useOperatorAction(STACK_BUILD_MUTATION);
  const up = useOperatorAction(STACK_UP_MUTATION);
  const down = useOperatorAction(STACK_DOWN_MUTATION);
  const destroy = useOperatorAction(STACK_DESTROY_MUTATION);
  const jobRun = useOperatorAction(JOB_RUN_MUTATION);
  const busy =
    build.result.fetching ||
    up.result.fetching ||
    down.result.fetching ||
    destroy.result.fetching ||
    jobRun.result.fetching;

  const runJob = useMemo(
    () => (job: JobState) => {
      void runDaemonAction({
        run: jobRun.run,
        field: "jobRun",
        variables: { name: job.name },
        label: t("operator.operations.run"),
        toast,
        refetch,
      });
    },
    [jobRun.run, refetch, t, toast],
  );

  const stackActions = useMemo<readonly StackAction[]>(
    () => [
      {
        field: "stackBuild",
        label: t("operator.operations.stack.build"),
        variant: "secondary",
        perform: () =>
          runDaemonAction({
            run: build.run,
            field: "stackBuild",
            variables: {},
            label: t("operator.operations.stack.build"),
            toast,
            refetch,
          }),
      },
      {
        field: "stackUp",
        label: t("operator.operations.stack.up"),
        variant: "secondary",
        perform: () =>
          runDaemonAction({
            run: up.run,
            field: "stackUp",
            variables: {},
            label: t("operator.operations.stack.up"),
            toast,
            refetch,
          }),
      },
      {
        field: "stackDown",
        label: t("operator.operations.stack.down"),
        variant: "ghost",
        perform: () =>
          runDaemonAction({
            run: down.run,
            field: "stackDown",
            variables: {},
            label: t("operator.operations.stack.down"),
            toast,
            refetch,
          }),
      },
      {
        field: "stackDestroy",
        label: t("operator.operations.stack.destroy"),
        variant: "ghost",
        dangerous: true,
        perform: () =>
          runDaemonAction({
            run: destroy.run,
            field: "stackDestroy",
            variables: { purge: false },
            label: t("operator.operations.stack.destroy"),
            toast,
            refetch,
          }),
      },
    ],
    [build.run, destroy.run, down.run, refetch, t, toast, up.run],
  );

  const runStack = useMemo(
    () => (action: StackAction) => {
      void (async () => {
        if (action.dangerous) {
          const ok = await confirm({
            title: t("operator.operations.stack.destroy.confirm.title"),
            body: t("operator.operations.stack.destroy.confirm.body"),
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

  return { runJob, stackActions, runStack, busy };
}
