import { useState, type ReactNode } from "react";

import {
  SOURCE_FETCH_MUTATION,
  SOURCE_PULL_MUTATION,
  SOURCE_PUSH_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { DaemonResourceTable, type DaemonResourceAction } from "../parts/DaemonResourceTable";
import { OperatorSection } from "../parts/OperatorSection";
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
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ sources: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchSource = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_FETCH_MUTATION);
  const pull = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PULL_MUTATION);
  const push = useOperatorAction<DaemonActionData, SourceActionVars>(SOURCE_PUSH_MUTATION);
  const busy = fetchSource.result.fetching || pull.result.fetching || push.result.fetching;

  const sources = snapshot?.sources ?? [];
  const actionDefs: readonly SourceAction[] = [
    { field: "sourceFetch", label: t("operator.sources.fetch"), variant: "secondary", run: fetchSource.run },
    { field: "sourcePull", label: t("operator.sources.pull"), variant: "ghost", run: pull.run },
    { field: "sourcePush", label: t("operator.sources.push"), variant: "ghost", run: push.run },
  ];
  const actions: readonly DaemonResourceAction<SourceState>[] = actionDefs.map((action) => ({
    label: action.label,
    variant: action.variant,
    run: (source) =>
      runDaemonAction({
        run: action.run,
        field: action.field,
        variables: { name: source.name },
        label: action.label,
        setError: setActionError,
        refetch,
      }),
  }));

  return (
    <OperatorSection
      title={t("section.operator.sources.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.sources.loading")}
      actionError={actionError}
    >
      <DaemonResourceTable
        actions={actions}
        actionsLabel={t("operator.table.actions")}
        busy={busy}
        columns={[
          {
            header: t("operator.sources.column.name"),
            cell: (source) => <span className="font-medium text-fg">{source.name}</span>,
          },
          {
            header: t("operator.sources.column.kind"),
            cell: (source) => <span className="text-13 text-fg-muted">{source.kind}</span>,
          },
          { header: t("operator.sources.column.status"), cell: (source) => <StateTag state={source.state ?? "unknown"} /> },
          {
            header: t("operator.sources.column.branch"),
            cell: (source) => <span className="text-13 text-fg-muted">{source.branch ?? "—"}</span>,
          },
          {
            header: t("operator.sources.column.aheadBehind"),
            align: "end",
            cell: (source) => (
              <span className="text-13 tabular-nums text-fg-muted">
                ↑{source.ahead ?? 0} ↓{source.behind ?? 0}
              </span>
            ),
          },
          {
            header: t("operator.sources.column.dirty"),
            cell: (source) => (
              <span className="text-13 text-fg-muted">
                {source.dirty ? t("operator.sources.dirty") : t("operator.sources.clean")}
              </span>
            ),
          },
        ]}
        emptyMessage={t("operator.sources.empty")}
        rowKey={(source) => source.name}
        rows={sources}
      />
    </OperatorSection>
  );
}
