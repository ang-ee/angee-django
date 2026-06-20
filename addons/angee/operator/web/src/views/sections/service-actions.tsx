import { Button, useConfirm, useToast } from "@angee/base";
import { useMemo, type ReactNode } from "react";

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
import { runDaemonAction } from "../parts/run-action";

/** A lifecycle action for a service: its label, tone, and bound handler. */
export interface ServiceRowAction {
  label: string;
  variant: "secondary" | "ghost";
  perform: (service: ServiceState) => void;
}

/**
 * The service lifecycle actions, each wrapped to confirm (when destructive),
 * run via {@link runDaemonAction}, and surface a failure as a toast — the live
 * snapshot then reflects the new state, so callers need no local result store.
 * Shared by the services list row, the detail page, and the embedded ServiceRow.
 */
export function useServiceActions(refetch: () => void): {
  actions: readonly ServiceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();

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
          await runDaemonAction({
            run,
            field,
            variables: { name: service.name },
            label,
            toast,
            refetch,
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
          void runDaemonAction({
            run: recreate.run,
            field: "stackUp",
            // Recreate rebuilds the image and recreates the container, so a service-template change
            // (Dockerfile or env) takes effect. The daemon exposes no per-service rebuild:
            // `serviceUp(name)` has no `build` arg, and only `stackUp(input: { build: true })`
            // rebuilds an image, so scope `stackUp` to this one service.
            variables: { input: { services: [service.name], build: true } },
            label: t("operator.services.recreate"),
            toast,
            refetch,
          });
        },
      },
      named("serviceStop", t("operator.services.stop"), "ghost", stop.run),
      named("serviceDestroy", t("operator.services.destroy"), "ghost", destroy.run, true),
    ] satisfies readonly ServiceRowAction[];
  }, [confirm, destroy.run, recreate.run, refetch, restart.run, start.run, stop.run, t, toast]);

  return { actions, busy };
}

/** A horizontal bar of a service's lifecycle action buttons. */
export function ServiceActions({
  actions,
  busy,
  service,
  className = "flex justify-end gap-1",
}: {
  actions: readonly ServiceRowAction[];
  busy: boolean;
  service: ServiceState;
  className?: string;
}): ReactNode {
  return (
    <div className={className}>
      {actions.map((action) => (
        <Button
          key={action.label}
          disabled={busy}
          onClick={() => action.perform(service)}
          size="sm"
          variant={action.variant}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}
