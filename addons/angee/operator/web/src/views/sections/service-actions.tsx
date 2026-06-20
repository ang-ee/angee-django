import { useConfirm } from "@angee/base";
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
    const named = (
      field: string,
      label: string,
      variant: ServiceRowAction["variant"],
      run: (variables: { name: string }) => Promise<object>,
      dangerous = false,
    ): ServiceRowAction => ({
      label,
      variant,
      perform: (service) => {
        void (async () => {
          if (dangerous) {
            const ok = await confirm({
              title: t("operator.services.destroy.confirm.title"),
              body: t("operator.services.destroy.confirm.body", { name: service.name }),
              confirm: label,
              danger: true,
            });
            if (!ok) return;
          }
          await runDaemon({
            run,
            field,
            variables: { name: service.name },
            label,
          });
        })();
      },
    });
    return [
      named("serviceStart", t("operator.services.start"), "secondary", start.run),
      named("serviceRestart", t("operator.services.restart"), "ghost", restart.run),
      {
        label: t("operator.services.recreate"),
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
            label: t("operator.services.recreate"),
          });
        },
      },
      named("serviceStop", t("operator.services.stop"), "ghost", stop.run),
      named("serviceDestroy", t("operator.services.destroy"), "ghost", destroy.run, true),
    ] satisfies readonly ServiceRowAction[];
  }, [confirm, destroy.run, recreate.run, restart.run, runDaemon, start.run, stop.run, t]);

  return { actions, busy };
}
