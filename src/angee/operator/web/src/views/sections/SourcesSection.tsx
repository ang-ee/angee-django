import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@angee/base";
import { useT } from "@angee/sdk";
import { useState, type ReactNode } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import { SectionError, SectionLoading } from "../parts/SectionStatus";
import { StateTag } from "../parts/StateTag";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface SourceActionVars extends Record<string, unknown> {
  name: string;
}
interface SourceAction {
  field: string;
  label: string;
  variant: "secondary" | "ghost";
  run: (variables: SourceActionVars) => Promise<DaemonActionData>;
}

/** Sources pane: cached git/local sources with fetch/pull/push + drift readout. */
export function SourcesSection(): ReactNode {
  const t = useT("operator");
  const { snapshot, result, refetch } = useOperatorSnapshot({ sources: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchSource = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PULL_MUTATION);
  const push = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading sources" />;
  }
  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }

  const sources = snapshot?.sources ?? [];
  const actions: readonly SourceAction[] = [
    { field: "sourceFetch", label: "Fetch", variant: "secondary", run: fetchSource.run },
    { field: "sourcePull", label: "Pull", variant: "ghost", run: pull.run },
    { field: "sourcePush", label: "Push", variant: "ghost", run: push.run },
  ];

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.sources.title")}</h2>
      {actionError ? <SectionError message={actionError} /> : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Kind</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Branch</TableHead>
            <TableHead className="text-right">Ahead/Behind</TableHead>
            <TableHead>Dirty</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sources.length === 0 ? (
            <TableRow>
              <TableCell className="text-center text-13 text-fg-muted" colSpan={7}>
                No sources.
              </TableCell>
            </TableRow>
          ) : (
            sources.map((source) => (
              <TableRow key={source.name}>
                <TableCell className="font-medium text-fg">{source.name}</TableCell>
                <TableCell className="text-13 text-fg-muted">{source.kind}</TableCell>
                <TableCell>
                  <StateTag state={source.state ?? "unknown"} />
                </TableCell>
                <TableCell className="text-13 text-fg-muted">{source.branch ?? "—"}</TableCell>
                <TableCell className="text-right text-13 tabular-nums text-fg-muted">
                  ↑{source.ahead ?? 0} ↓{source.behind ?? 0}
                </TableCell>
                <TableCell className="text-13 text-fg-muted">
                  {source.dirty ? "dirty" : "clean"}
                </TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    {actions.map((action) => (
                      <Button
                        disabled={busy}
                        key={action.field}
                        onClick={() =>
                          void runDaemonAction({
                            run: action.run,
                            field: action.field,
                            variables: { name: source.name },
                            label: action.label,
                            setError: setActionError,
                            refetch,
                          })
                        }
                        size="sm"
                        variant={action.variant}
                      >
                        {action.label}
                      </Button>
                    ))}
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}
