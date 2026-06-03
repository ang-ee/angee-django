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
  SERVICE_RESTART_MUTATION,
  SERVICE_START_MUTATION,
  SERVICE_STOP_MUTATION,
} from "../../data/documents";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import { SectionError, SectionLoading } from "../parts/SectionStatus";
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
  const t = useT("operator");
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const start = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_START_MUTATION);
  const stop = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_STOP_MUTATION);
  const restart = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_RESTART_MUTATION);
  const busy = start.result.fetching || stop.result.fetching || restart.result.fetching;

  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }
  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading services" />;
  }

  const services = snapshot?.services ?? [];
  const actions: readonly ServiceAction[] = [
    { field: "serviceStart", label: "Start", variant: "secondary", run: start.run },
    { field: "serviceRestart", label: "Restart", variant: "ghost", run: restart.run },
    { field: "serviceStop", label: "Stop", variant: "ghost", run: stop.run },
  ];

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.services.title")}</h2>
      {actionError ? <SectionError message={actionError} /> : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Runtime</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Health</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {services.length === 0 ? (
            <TableRow>
              <TableCell className="text-center text-13 text-fg-muted" colSpan={5}>
                No services.
              </TableCell>
            </TableRow>
          ) : (
            services.map((service) => (
              <TableRow key={service.name}>
                <TableCell className="font-medium text-fg">{service.name}</TableCell>
                <TableCell className="text-13 text-fg-muted">{service.runtime}</TableCell>
                <TableCell>
                  <StateTag state={service.status} />
                </TableCell>
                <TableCell className="text-13 text-fg-muted">{service.health ?? "—"}</TableCell>
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
                            variables: { name: service.name },
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
