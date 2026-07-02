import { useConfirm } from "@angee/ui";
import { useMemo } from "react";

import {
  SERVICE_DESTROY_MUTATION,
  SERVICE_RESTART_MUTATION,
  SERVICE_START_MUTATION,
  SERVICE_STOP_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { ServiceState } from "../../data/types";
import { useRunDaemonAction } from "../parts/run-action";
import type { RowAction } from "../parts/RowActions";

/** A lifecycle action for a service: its label, tone, and bound handler. */
export type ServiceRowAction = RowAction<ServiceState>;

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
  const recreate = useOperatorAction(STACK_UP_MUTATION);
  const destroy = useOperatorAction(SERVICE_DESTROY_MUTATION);
  const busy =
    start.result.fetching ||
    stop.result.fetching ||
    restart.result.fetching ||
    recreate.result.fetching ||
    destroy.result.fetching;

  const actions = useMemo<readonly ServiceRowAction[]>(() => {
    const named = <V extends Record<string, unknown>>(
      field: string,
      label: string,
      variant: ServiceRowAction["variant"],
      run: (variables: V) => Promise<object | undefined>,
      variablesFor: (service: ServiceState) => V,
      dangerous = false,
    ): ServiceRowAction => ({
      label,
      variant,
      perform: (service) => {
        void (async () => {
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
        })();
      },
    });
    return [
      named(
        "serviceStart",
        t("services.start"),
        "secondary",
        start.run,
        (service) => ({ name: service.name }),
      ),
      named(
        "serviceRestart",
        t("services.restart"),
        "ghost",
        restart.run,
        (service) => ({ name: service.name }),
      ),
      {
        label: t("services.recreate"),
        variant: "ghost",
        perform: (service) => {
          void runDaemon({
            run: recreate.run,
            field: "stackUp",
            // Recreate rebuilds the image and recreates the container, so a service-template change
            // (Dockerfile or env) takes effect. The daemon exposes no per-service rebuild:
            // `serviceUp(name)` has no `build` arg, and only `stackUp(input: { build: true })`
            // rebuilds an image, so scope `stackUp` to this one service.
            variables: { input: { services: [service.name], build: true } },
            label: t("services.recreate"),
          });
        },
      },
      named(
        "serviceStop",
        t("services.stop"),
        "ghost",
        stop.run,
        (service) => ({ name: service.name }),
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
  }, [confirm, destroy.run, recreate.run, restart.run, runDaemon, start.run, stop.run, t]);

  return { actions, busy };
}
