import { Button, RowsListView, useToast, type ListColumn } from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { StateTag } from "../parts/StateTag";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface SourceActionVars extends Record<string, unknown> {
  name: string;
}

/** A source action rendered per row: its label, tone, and handler. */
interface SourceRowAction {
  label: string;
  variant: "secondary" | "ghost";
  perform: (source: SourceState) => void;
}

// RowsListView keys rows by `id`; the daemon identifies a source by name.
type SourceRowData = SourceState & { id: string };

/** Sources pane: cached git/local sources with fetch/pull/push + drift readout. */
export function SourcesSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ sources: true });
  const { actions, busy } = useSourceActions(refetch);

  const rows = useMemo<readonly SourceRowData[]>(
    () => (snapshot?.sources ?? []).map((source) => ({ ...source, id: source.name })),
    [snapshot],
  );

  const columns = useMemo<readonly ListColumn<SourceRowData>[]>(
    () => [
      {
        field: "name",
        header: t("operator.sources.column.name"),
        render: (source) => <span className="font-medium text-fg">{source.name}</span>,
      },
      {
        field: "kind",
        header: t("operator.sources.column.kind"),
        render: (source) => <span className="text-13 text-fg-muted">{source.kind}</span>,
      },
      {
        field: "status",
        header: t("operator.sources.column.status"),
        render: (source) => <StateTag state={source.state ?? "unknown"} />,
      },
      {
        field: "branch",
        header: t("operator.sources.column.branch"),
        render: (source) => <span className="text-13 text-fg-muted">{source.branch ?? "—"}</span>,
      },
      {
        field: "aheadBehind",
        header: t("operator.sources.column.aheadBehind"),
        align: "right",
        render: (source) => (
          <span className="text-13 tabular-nums text-fg-muted">
            ↑{source.ahead ?? 0} ↓{source.behind ?? 0}
          </span>
        ),
      },
      {
        field: "dirty",
        header: t("operator.sources.column.dirty"),
        render: (source) => (
          <span className="text-13 text-fg-muted">
            {source.dirty ? t("operator.sources.dirty") : t("operator.sources.clean")}
          </span>
        ),
      },
      {
        field: "actions",
        header: t("operator.table.actions"),
        sortable: false,
        align: "right",
        render: (source) => <SourceActions actions={actions} busy={busy} source={source} />,
      },
    ],
    [actions, busy, t],
  );

  return (
    <RowsListView<SourceRowData>
      rows={rows}
      columns={columns}
      fetching={result.fetching}
      error={snapshot ? null : result.error}
      emptyMessage={t("operator.sources.empty")}
    />
  );
}

/**
 * The three source actions, each run via {@link runDaemonAction} and surfacing a
 * failure as a toast — the live snapshot then reflects the new state, so the row
 * needs no local result store.
 */
function useSourceActions(refetch: () => void): {
  actions: readonly SourceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const toast = useToast();

  const fetchSource = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PULL_MUTATION);
  const push = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  const actions = useMemo<readonly SourceRowAction[]>(() => {
    const defs = [
      { field: "sourceFetch", label: t("operator.sources.fetch"), variant: "secondary" as const, run: fetchSource.run },
      { field: "sourcePull", label: t("operator.sources.pull"), variant: "ghost" as const, run: pull.run },
      { field: "sourcePush", label: t("operator.sources.push"), variant: "ghost" as const, run: push.run },
    ];
    return defs.map((def) => ({
      label: def.label,
      variant: def.variant,
      perform: (source: SourceState) => {
        void runDaemonAction({
          run: def.run,
          field: def.field,
          variables: { name: source.name },
          label: def.label,
          setError: (message) => {
            if (message) toast.danger({ title: message });
          },
          refetch,
        });
      },
    }));
  }, [fetchSource.run, pull.run, push.run, refetch, t, toast]);

  return { actions, busy };
}

function SourceActions({
  actions,
  busy,
  source,
}: {
  actions: readonly SourceRowAction[];
  busy: boolean;
  source: SourceState;
}): ReactNode {
  return (
    <div className="flex justify-end gap-1">
      {actions.map((action) => (
        <Button
          key={action.label}
          disabled={busy}
          onClick={() => action.perform(source)}
          size="sm"
          variant={action.variant}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}
