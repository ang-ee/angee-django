import { useConfirm } from "@angee/ui";
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
import { useRunDaemonAction } from "../parts/run-action";
import type { OperatorAction } from "../parts/OperatorActionButtons";

/** A lifecycle action for a service: its label, tone, and bound handler. */
export type ServiceRowAction = OperatorAction<ServiceState>;

/** Service lifecycle actions shared by the list row, detail page, and embedded ServiceRow. */
export function useServiceActions(refetch: () => void): {
  actions: readonly ServiceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const runDaemon = useRunDaemonAction(refetch);

  const start = useOperatorAction(SERVICE_START_MUTATION);
  const stop = useOperatorAction(SERVICE_STOP_MUTATION);
  const restart = useOperatorAction(SERVICE_RESTART_MUTATION);
  const destroy = useOperatorAction(SERVICE_DESTROY_MUTATION);
  const busy =
    start.result.fetching ||
    stop.result.fetching ||
    restart.result.fetching ||
    destroy.result.fetching;

  const actions = useMemo<readonly ServiceRowAction[]>(() => {
    const named = <V extends Record<string, unknown>>(
      field: string,
      label: string,
      variant: ServiceRowAction["variant"],
      run: (variables: V) => Promise<object | undefined>,
      variablesFor: (service: ServiceState) => V,
      dangerous = false,
      visible?: (service: ServiceState) => boolean,
    ): ServiceRowAction => ({
      label,
      variant,
      visible,
      perform: async (service) => {
        if (dangerous) {
          const ok = await confirm({
            title: t("services.destroy.confirm.title"),
            body: t("services.destroy.confirm.body", { name: service.name }),
            confirm: label,
            danger: true,
          });
          if (!ok) return;
        }
        await runDaemon({
          run,
          field,
          variables: variablesFor(service),
          label,
        });
      },
    });
    return [
      named(
        "serviceStart",
        t("services.start"),
        "secondary",
        start.run,
        (service) => ({ name: service.name }),
        false,
        (service) => !serviceActive(service),
      ),
      named(
        "serviceRestart",
        t("services.restart"),
        "ghost",
        restart.run,
        (service) => ({ name: service.name }),
        false,
        serviceActive,
      ),
      named(
        "serviceStop",
        t("services.stop"),
        "ghost",
        stop.run,
        (service) => ({ name: service.name }),
        false,
        serviceActive,
      ),
      named(
        "delete_services_by_pk",
        t("services.destroy"),
        "ghost",
        destroy.run,
        (service) => ({ id: service.id }),
        true,
      ),
    ] satisfies readonly ServiceRowAction[];
  }, [confirm, destroy.run, restart.run, runDaemon, start.run, stop.run, t]);

  return { actions, busy };
}
