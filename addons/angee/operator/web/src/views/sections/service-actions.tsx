import { useMemo } from "react";

import {
  SERVICE_DESTROY_MUTATION,
  SERVICE_RESTART_MUTATION,
  SERVICE_START_MUTATION,
  SERVICE_STOP_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { serviceActive } from "../../data/service-state";
import { useOperatorAction } from "../../data/transport";
import type { ServiceState } from "../../data/types";
import { useDaemonMutationRun, type DaemonVerb } from "../parts/daemon-verbs";

/** Service lifecycle verbs shared by the list rows, the record page and the embedded ServiceRow. */
export function useServiceActions(refetch: () => void): {
  actions: readonly DaemonVerb<ServiceState>[];
  busy: boolean;
} {
  const t = useOperatorT();
  const runDaemon = useDaemonMutationRun(refetch);

  const start = useOperatorAction(SERVICE_START_MUTATION);
  const stop = useOperatorAction(SERVICE_STOP_MUTATION);
  const restart = useOperatorAction(SERVICE_RESTART_MUTATION);
  const destroy = useOperatorAction(SERVICE_DESTROY_MUTATION);
  const busy =
    start.result.fetching ||
    stop.result.fetching ||
    restart.result.fetching ||
    destroy.result.fetching;

  const actions = useMemo<readonly DaemonVerb<ServiceState>[]>(() => [
    {
      id: "start", label: t("services.start"), primary: true,
      visible: (service) => !serviceActive(service),
      perform: (service) => runDaemon({ run: start.run, field: "serviceStart", variables: { name: service.name } }),
    },
    {
      id: "restart", label: t("services.restart"), primary: true,
      visible: serviceActive,
      perform: (service) => runDaemon({ run: restart.run, field: "serviceRestart", variables: { name: service.name } }),
    },
    {
      id: "stop", label: t("services.stop"),
      visible: serviceActive,
      perform: (service) => runDaemon({ run: stop.run, field: "serviceStop", variables: { name: service.name } }),
    },
    {
      id: "destroy", label: t("services.destroy"), danger: true, placement: "menu",
      confirm: { title: t("services.destroy.confirm.title"), body: (service) => t("services.destroy.confirm.body", { name: service.name }) },
      perform: (service) => runDaemon({ run: destroy.run, field: "delete_services_by_pk", variables: { id: service.id } }),
    },
  ], [destroy.run, restart.run, runDaemon, start.run, stop.run, t]);

  return { actions, busy };
}
