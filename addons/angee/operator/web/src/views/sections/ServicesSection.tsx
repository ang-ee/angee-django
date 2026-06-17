import {
  Button,
  RowsListView,
  Skeleton,
  useConfirm,
  useToast,
  type ListColumn,
} from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  SERVICE_DESTROY_MUTATION,
  SERVICE_RESTART_MUTATION,
  SERVICE_START_MUTATION,
  SERVICE_STOP_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { ServiceState } from "../../data/types";
import { OperatorSection } from "../parts/OperatorSection";
import { StateTag } from "../parts/StateTag";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface ServiceActionVars extends Record<string, unknown> {
  name: string;
}

/** A lifecycle action rendered per service row: its label, tone, and handler. */
interface ServiceRowAction {
  label: string;
  variant: "secondary" | "ghost";
  perform: (service: ServiceState) => void;
}

// RowsListView keys rows by `id`; the daemon identifies a service by name.
type ServiceRowData = ServiceState & { id: string };

export interface ServicesSectionProps {
  /** Restrict the list to these service names; omit to show every service. */
  names?: readonly string[];
  /** Retained for API compatibility; the console nav owns the page heading. */
  title?: ReactNode;
}

/** Services pane: the daemon service list with lifecycle actions. */
export function ServicesSection({ names }: ServicesSectionProps = {}): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);

  const rows = useMemo<readonly ServiceRowData[]>(
    () =>
      (snapshot?.services ?? [])
        .filter((service) => names === undefined || names.includes(service.name))
        .map((service) => ({ ...service, id: service.name })),
    [names, snapshot],
  );

  const columns = useMemo<readonly ListColumn<ServiceRowData>[]>(
    () => [
      {
        field: "name",
        header: t("operator.services.column.name"),
        render: (service) => <span className="font-medium text-fg">{service.name}</span>,
      },
      {
        field: "runtime",
        header: t("operator.services.column.runtime"),
        render: (service) => <span className="text-13 text-fg-muted">{service.runtime}</span>,
      },
      {
        field: "status",
        header: t("operator.services.column.status"),
        render: (service) => <StateTag state={service.status} />,
      },
      {
        field: "health",
        header: t("operator.services.column.health"),
        render: (service) => <span className="text-13 text-fg-muted">{service.health ?? "—"}</span>,
      },
      {
        field: "actions",
        header: t("operator.table.actions"),
        sortable: false,
        align: "right",
        render: (service) => (
          <ServiceActions actions={actions} busy={busy} service={service} />
        ),
      },
    ],
    [actions, busy, t],
  );

  return (
    <RowsListView<ServiceRowData>
      rows={rows}
      columns={columns}
      fetching={result.fetching}
      error={snapshot ? null : result.error}
      emptyMessage={t("operator.services.empty")}
    />
  );
}

export interface ServiceRowProps {
  /** The single service name owned by the embedding object. */
  name: string;
  /** Optional empty-state text when the daemon has not rendered the service yet. */
  emptyMessage?: ReactNode;
}

/** Compact single-service row for views that already own the service identity. */
export function ServiceRow({ name, emptyMessage }: ServiceRowProps): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const service = (snapshot?.services ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <OperatorSection
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.services.loading")}
      loadingContent={<ServiceRowSkeleton />}
    >
      {service ? (
        <ServiceControlRow actions={actions} busy={busy} service={service} />
      ) : (
        <p className="border-y border-border-subtle py-3 text-13 text-fg-muted">
          {emptyMessage ?? t("operator.services.empty")}
        </p>
      )}
    </OperatorSection>
  );
}

/**
 * The four service lifecycle actions, each wrapped to confirm (when destructive),
 * run via {@link runDaemonAction}, and surface a failure as a toast — the live
 * snapshot then reflects the new state, so the row needs no local result store.
 */
function useServiceActions(refetch: () => void): {
  actions: readonly ServiceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();

  const start = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_START_MUTATION);
  const stop = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_STOP_MUTATION);
  const restart = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_RESTART_MUTATION);
  const destroy = useOperatorAction<DaemonActionData, ServiceActionVars>(SERVICE_DESTROY_MUTATION);
  const busy =
    start.result.fetching ||
    stop.result.fetching ||
    restart.result.fetching ||
    destroy.result.fetching;

  const actions = useMemo<readonly ServiceRowAction[]>(() => {
    const defs = [
      { field: "serviceStart", label: t("operator.services.start"), variant: "secondary" as const, run: start.run },
      { field: "serviceRestart", label: t("operator.services.restart"), variant: "ghost" as const, run: restart.run },
      { field: "serviceStop", label: t("operator.services.stop"), variant: "ghost" as const, run: stop.run },
      {
        field: "serviceDestroy",
        label: t("operator.services.destroy"),
        variant: "ghost" as const,
        dangerous: true,
        run: destroy.run,
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
            variables: { name: service.name },
            label: def.label,
            setError: (message) => {
              if (message) toast.danger({ title: message });
            },
            refetch,
          });
        })();
      },
    }));
  }, [confirm, destroy.run, refetch, restart.run, start.run, stop.run, t, toast]);

  return { actions, busy };
}

function ServiceActions({
  actions,
  busy,
  service,
}: {
  actions: readonly ServiceRowAction[];
  busy: boolean;
  service: ServiceState;
}): ReactNode {
  return (
    <div className="flex justify-end gap-1">
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

function ServiceControlRow({
  actions,
  busy,
  service,
}: {
  actions: readonly ServiceRowAction[];
  busy: boolean;
  service: ServiceState;
}): ReactNode {
  return (
    <div
      className={
        "grid min-w-0 grid-cols-[minmax(0,1fr)_7rem_8rem_max-content] " +
        "items-center gap-6 border-y border-border-subtle py-2 text-13"
      }
    >
      <span className="min-w-0 truncate font-medium text-fg">{service.name}</span>
      <span className="whitespace-nowrap text-fg-muted">{service.runtime}</span>
      <span className="whitespace-nowrap">
        <StateTag state={service.status} />
      </span>
      <ServiceActions actions={actions} busy={busy} service={service} />
    </div>
  );
}

function ServiceRowSkeleton(): ReactNode {
  return (
    <div
      aria-hidden="true"
      className={
        "grid min-w-0 grid-cols-[minmax(0,1fr)_7rem_8rem_max-content] " +
        "items-center gap-6 border-y border-border-subtle py-2"
      }
    >
      <Skeleton shape="text" size="sm" className="h-5" />
      <Skeleton shape="text" size="sm" className="h-5" />
      <Skeleton shape="text" size="sm" className="h-6" />
      <div className="flex shrink-0 justify-end gap-1">
        <Skeleton className="h-btn-sm w-14" />
        <Skeleton className="h-btn-sm w-16" />
        <Skeleton className="h-btn-sm w-14" />
      </div>
    </div>
  );
}
