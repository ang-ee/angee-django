import { RowsListView, useRouteHref, type ResourceToolbarGroupOption, type ListColumn } from "@angee/ui";
import { useCallback, useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { OperatorRowsList, type OperatorRowsSelector } from "../parts/operator-rows";
import type { ServiceState } from "../../data/types";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { verbRowActions } from "../parts/daemon-verbs";
import { ServiceStatus } from "../parts/ServiceStatus";
import { useServiceActions } from "./service-actions";

type ServiceRowData = DaemonRow<ServiceState>;

/** The service list's columns, shared by the page and the embedded single-service row. */
function useServiceColumns(): readonly ListColumn<ServiceRowData>[] {
  const t = useOperatorT();
  return useMemo(() => [
    { field: "name", header: t("services.column.name") },
    { field: "runtime", header: t("services.column.runtime") },
    { field: "outcome", header: t("services.column.status"), render: (service) => <ServiceStatus service={service} /> },
    { field: "health", header: t("services.column.health") },
  ], [t]);
}

export interface ServicesPageProps {
  /** Restrict the list to these service names; omit to show every service. */
  names?: readonly string[];
}

/** Services page: the daemon service list. Rows open the service record page. */
export function ServicesPage({ names }: ServicesPageProps = {}): ReactNode {
  const t = useOperatorT();
  const routeHref = useRouteHref();
  const { refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const selectRows = useCallback<OperatorRowsSelector<ServiceRowData>>(
    (snapshot) => daemonRowsByName(
      snapshot.services.filter((service) => names === undefined || names.includes(service.name)),
    ),
    [names],
  );
  const columns = useServiceColumns();
  const groupOptions: readonly ResourceToolbarGroupOption[] = useMemo(
    () => [
      { id: "status", label: t("services.column.status"), group: { field: "outcome" }, type: "value" },
      { id: "runtime", label: t("services.column.runtime"), group: { field: "runtime" }, type: "value" },
    ],
    [t],
  );
  const rowActions = useMemo(() => verbRowActions(actions, busy), [actions, busy]);

  return (
    <OperatorRowsList<ServiceRowData>
      sections={{ services: true }}
      selectRows={selectRows}
      columns={columns}
      groupOptions={groupOptions}
      rowHref={(service) => routeHref("operator.services.detail", { name: service.name })}
      rowActions={rowActions}
      emptyContent={t("services.empty")}
    />
  );
}

export interface ServiceRowProps {
  /** The single service name owned by the embedding object. */
  name: string;
  /** Optional empty-state text when the daemon has not rendered the service yet. */
  emptyContent?: ReactNode;
}

/** One service as an embedded row of the shared list, for views that already own the service identity. */
export function ServiceRow({ name, emptyContent }: ServiceRowProps): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const columns = useServiceColumns();
  const rowActions = useMemo(() => verbRowActions(actions, busy), [actions, busy]);
  const rows = useMemo(
    () => daemonRowsByName((snapshot?.services ?? []).filter((candidate) => candidate.name === name)),
    [name, snapshot],
  );
  return (
    <RowsListView<ServiceRowData>
      presentation="embedded"
      scope="local"
      selectable={false}
      rows={rows}
      columns={columns}
      rowActions={rowActions}
      fetching={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      emptyContent={emptyContent ?? t("services.empty")}
    />
  );
}
