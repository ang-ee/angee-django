import { useState, type ReactNode } from "react";

import {
  SERVICE_RESTART_MUTATION,
  SERVICE_START_MUTATION,
  SERVICE_STOP_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { ServiceState } from "../../data/types";
import { DaemonResourceTable, type DaemonResourceAction } from "../parts/DaemonResourceTable";
import { OperatorSection } from "../parts/OperatorSection";
import { StateTag } from "../parts/StateTag";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface ServiceActionVars extends Record<string, unknown> {
  name: string;
}
interface ServiceAction {
  field: string;
  label: string;
  variant: "secondary" | "ghost";
  run: (variables: ServiceActionVars) => Promise<DaemonActionData>;
}

/** Services pane: a daemon service table with lifecycle actions. */
export function ServicesSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const start = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_START_MUTATION);
  const stop = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_STOP_MUTATION);
  const restart = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_RESTART_MUTATION);
  const busy = start.result.fetching || stop.result.fetching || restart.result.fetching;

  const services = snapshot?.services ?? [];
  const actionDefs: readonly ServiceAction[] = [
    { field: "serviceStart", label: t("operator.services.start"), variant: "secondary", run: start.run },
    { field: "serviceRestart", label: t("operator.services.restart"), variant: "ghost", run: restart.run },
    { field: "serviceStop", label: t("operator.services.stop"), variant: "ghost", run: stop.run },
  ];
  const actions: readonly DaemonResourceAction<ServiceState>[] = actionDefs.map((action) => ({
    label: action.label,
    variant: action.variant,
    run: (service) =>
      runDaemonAction({
        run: action.run,
        field: action.field,
        variables: { name: service.name },
        label: action.label,
        setError: setActionError,
        refetch,
      }),
  }));

  return (
    <OperatorSection
      title={t("section.operator.services.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.services.loading")}
      actionError={actionError}
    >
      <DaemonResourceTable
        actions={actions}
        actionsLabel={t("operator.table.actions")}
        busy={busy}
        columns={[
          {
            header: t("operator.services.column.name"),
            cell: (service) => <span className="font-medium text-fg">{service.name}</span>,
          },
          {
            header: t("operator.services.column.runtime"),
            cell: (service) => <span className="text-13 text-fg-muted">{service.runtime}</span>,
          },
          { header: t("operator.services.column.status"), cell: (service) => <StateTag state={service.status} /> },
          {
            header: t("operator.services.column.health"),
            cell: (service) => (
              <span className="text-13 text-fg-muted">{service.health ?? "—"}</span>
            ),
          },
        ]}
        emptyMessage={t("operator.services.empty")}
        rowKey={(service) => service.name}
        rows={services}
      />
    </OperatorSection>
  );
}
