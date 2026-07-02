import { Button, Card, CardContent, CardHeader, CardTitle, RowsListView, textRoleVariants, useConfirm, type ListColumn } from "@angee/ui";
import { useMemo, type ReactNode } from "react";

import {
  JOB_RUN_MUTATION,
  STACK_BUILD_MUTATION,
  STACK_DESTROY_MUTATION,
  STACK_DOWN_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
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

/** Operations page: the daemon job list with run + stack lifecycle controls. */
export function OperationsPage(): ReactNode {
  const t = useOperatorT();
  const { rows, fetching, error, refetch } = useOperatorRows(
    { operations: true },
    (snapshot) => daemonRowsByName(snapshot.jobs),
  );
  const { runJob, stackActions, runStack, busy } = useOperationActions(refetch);

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
      {
        field: "actions",
        header: t("table.actions"),
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
              {t("operations.run")}
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
    </>
  );
}

/** Operations actions: per-job run plus stack lifecycle controls. */
function useOperationActions(refetch: () => void): {
  runJob: (job: JobState) => void;
  stackActions: readonly StackAction[];
  runStack: (action: StackAction) => void;
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const runDaemon = useRunDaemonAction(refetch);

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
      void runDaemon({
        run: jobRun.run,
        field: "jobRun",
        variables: { name: job.name },
        label: t("operations.run"),
      });
    },
    [jobRun.run, runDaemon, t],
  );

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

  return { runJob, stackActions, runStack, busy };
}
