import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useConfirm,
} from "@angee/base";
import { useT } from "@angee/sdk";
import { useState, type ReactNode } from "react";

import {
  JOB_RUN_MUTATION,
  STACK_BUILD_MUTATION,
  STACK_DESTROY_MUTATION,
  STACK_DOWN_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import { SectionError, SectionLoading } from "../parts/SectionStatus";
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
  const t = useT("operator");
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

  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }
  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading operations" />;
  }

  const jobs = snapshot?.jobs ?? [];
  const stackActions: readonly StackAction[] = [
    { field: "stackBuild", label: "Build", variant: "secondary", variables: {}, run: build.run },
    { field: "stackUp", label: "Up", variant: "secondary", variables: {}, run: up.run },
    { field: "stackDown", label: "Down", variant: "ghost", variables: {}, run: down.run },
    {
      field: "stackDestroy",
      label: "Destroy",
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
          title: "Destroy stack?",
          body: "All services and runtime state are removed. This cannot be undone.",
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
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.operations.title")}</h2>
      {actionError ? <SectionError message={actionError} /> : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Runtime</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {jobs.length === 0 ? (
            <TableRow>
              <TableCell className="text-center text-13 text-fg-muted" colSpan={3}>
                No jobs.
              </TableCell>
            </TableRow>
          ) : (
            jobs.map((job) => (
              <TableRow key={job.name}>
                <TableCell className="font-medium text-fg">{job.name}</TableCell>
                <TableCell className="text-13 text-fg-muted">{job.runtime}</TableCell>
                <TableCell className="text-right">
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void runDaemonAction({
                        run: jobRun.run,
                        field: "jobRun",
                        variables: { name: job.name },
                        label: "Run",
                        setError: setActionError,
                        refetch,
                      })
                    }
                    size="sm"
                    variant="secondary"
                  >
                    Run
                  </Button>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      <Card>
        <CardHeader>
          <CardTitle>Stack lifecycle</CardTitle>
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
    </div>
  );
}
