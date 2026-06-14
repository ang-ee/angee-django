import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  useConfirm,
} from "@angee/base";
import { useState, type ReactNode } from "react";

import {
  JOB_RUN_MUTATION,
  STACK_BUILD_MUTATION,
  STACK_DESTROY_MUTATION,
  STACK_DOWN_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import { DaemonResourceTable } from "../parts/DaemonResourceTable";
import { OperatorSection } from "../parts/OperatorSection";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

type StackVars = Record<string, unknown>;
interface JobVars extends Record<string, unknown> {
  name: string;
}
interface StackAction {
  field: string;
  label: string;
  variant: "secondary" | "ghost";
  variables: StackVars;
  /** Destructive — require a styled confirmation first. */
  dangerous?: boolean;
  run: (variables: StackVars) => Promise<DaemonActionData>;
}

/** Operations pane: a daemon job table with run + stack lifecycle controls. */
export function OperationsSection(): ReactNode {
  const t = useOperatorT();
  const confirm = useConfirm();
  const { snapshot, result, refetch } = useOperatorSnapshot({ operations: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const build = useOperatorAction<DaemonActionData, StackVars>(STACK_BUILD_MUTATION);
  const up = useOperatorAction<DaemonActionData, StackVars>(STACK_UP_MUTATION);
  const down = useOperatorAction<DaemonActionData, StackVars>(STACK_DOWN_MUTATION);
  const destroy = useOperatorAction<DaemonActionData, StackVars>(STACK_DESTROY_MUTATION);
  const jobRun = useOperatorAction<DaemonActionData, JobVars>(JOB_RUN_MUTATION);
  const busy =
    build.result.fetching ||
    up.result.fetching ||
    down.result.fetching ||
    destroy.result.fetching ||
    jobRun.result.fetching;

  const jobs = snapshot?.jobs ?? [];
  const stackActions: readonly StackAction[] = [
    { field: "stackBuild", label: t("operator.operations.stack.build"), variant: "secondary", variables: {}, run: build.run },
    { field: "stackUp", label: t("operator.operations.stack.up"), variant: "secondary", variables: {}, run: up.run },
    { field: "stackDown", label: t("operator.operations.stack.down"), variant: "ghost", variables: {}, run: down.run },
    {
      field: "stackDestroy",
      label: t("operator.operations.stack.destroy"),
      variant: "ghost",
      variables: { purge: false },
      dangerous: true,
      run: destroy.run,
    },
  ];

  function runStack(action: StackAction): void {
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
      await runDaemonAction({
        run: action.run,
        field: action.field,
        variables: action.variables,
        label: action.label,
        setError: setActionError,
        refetch,
      });
    })();
  }

  return (
    <OperatorSection
      title={t("section.operator.operations.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.operations.loading")}
      actionError={actionError}
    >
      <DaemonResourceTable
        actions={[
          {
            label: t("operator.operations.run"),
            variant: "secondary",
            run: (job) =>
              runDaemonAction({
                run: jobRun.run,
                field: "jobRun",
                variables: { name: job.name },
                label: t("operator.operations.run"),
                setError: setActionError,
                refetch,
              }),
          },
        ]}
        actionsLabel={t("operator.table.actions")}
        busy={busy}
        columns={[
          {
            header: t("operator.operations.column.name"),
            cell: (job) => <span className="font-medium text-fg">{job.name}</span>,
          },
          {
            header: t("operator.operations.column.runtime"),
            cell: (job) => <span className="text-13 text-fg-muted">{job.runtime}</span>,
          },
        ]}
        emptyMessage={t("operator.operations.empty")}
        rowKey={(job) => job.name}
        rows={jobs}
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
    </OperatorSection>
  );
}
