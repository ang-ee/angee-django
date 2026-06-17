import { Button, useConfirm, useToast } from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  SERVICE_DESTROY_MUTATION,
  SERVICE_RESTART_MUTATION,
  SERVICE_START_MUTATION,
  SERVICE_STOP_MUTATION,
  STACK_UP_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { ServiceState } from "../../data/types";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

// Permissive, like the stack actions' vars (OperationsSection): the per-service daemon ops
// take `{ name }`, while Recreate takes stackUp's `{ input }`. Each call's GraphQL document
// types the actual variables; this is just the shared run-handler shape.
type ServiceActionVars = Record<string, unknown>;

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

  const start = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_START_MUTATION);
  const stop = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_STOP_MUTATION);
  const restart = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_RESTART_MUTATION);
  const recreate = useOperatorAction<DaemonActionData, ServiceActionVars>(STACK_UP_MUTATION);
  const destroy = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_DESTROY_MUTATION);
  const busy =
    start.result.fetching ||
    stop.result.fetching ||
    restart.result.fetching ||
    recreate.result.fetching ||
    destroy.result.fetching;

  const actions = useMemo<readonly ServiceRowAction[]>(() => {
    const named = (s: ServiceState): ServiceActionVars => ({ name: s.name });
    const defs = [
      { field: "serviceStart", label: t("operator.services.start"), variant: "secondary" as const, run: start.run, variables: named },
      { field: "serviceRestart", label: t("operator.services.restart"), variant: "ghost" as const, run: restart.run, variables: named },
      // Recreate rebuilds the image and recreates the container, so a service-template change
      // (Dockerfile or env) takes effect — a plain Restart reuses the cached image. The daemon
      // exposes no per-service rebuild: `serviceUp(name)` has no `build` arg, and only
      // `stackUp(input: { build: true })` rebuilds an image — so scope `stackUp` to this one
      // service (`compose up --build <service>`). If the daemon gains `serviceUp(build:)` or a
      // `serviceRecreate(name)`, switch this to that per-service owner.
      {
        field: "stackUp",
        label: t("operator.services.recreate"),
        variant: "ghost" as const,
        run: recreate.run,
        variables: (s: ServiceState): ServiceActionVars => ({ input: { services: [s.name], build: true } }),
      },
      { field: "serviceStop", label: t("operator.services.stop"), variant: "ghost" as const, run: stop.run, variables: named },
      {
        field: "serviceDestroy",
        label: t("operator.services.destroy"),
        variant: "ghost" as const,
        dangerous: true,
        run: destroy.run,
        variables: named,
      },
    ];
    return defs.map((def) => ({
      label: def.label,
      variant: def.variant,
      perform: (service: ServiceState) => {
        void (async () => {
          if (def.dangerous) {
            const ok = await confirm({
              title: t("operator.services.destroy.confirm.title"),
              body: t("operator.services.destroy.confirm.body", { name: service.name }),
              confirm: def.label,
              danger: true,
            });
            if (!ok) return;
          }
          await runDaemonAction({
            run: def.run,
            field: def.field,
            variables: def.variables(service),
            label: def.label,
            setError: (message) => {
              if (message) toast.danger({ title: message });
            },
            refetch,
          });
        })();
      },
    }));
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
