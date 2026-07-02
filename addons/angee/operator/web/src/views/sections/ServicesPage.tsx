import { cn, Skeleton, textRoleVariants, type ResourceToolbarGroupOption, type ListColumn } from "@angee/ui";
import { useCallback, useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import {
  OperatorRowsList,
  type OperatorRowsSelector,
} from "../parts/operator-rows";
import type { ServiceState } from "../../data/types";
import { serviceDetailPath } from "../../lib/paths";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { OperatorSection } from "../parts/OperatorSection";
import { RowActions } from "../parts/RowActions";
import { StateTag } from "../parts/StateTag";
import { useServiceActions } from "./service-actions";

type ServiceRowData = DaemonRow<ServiceState>;

export interface ServicesPageProps {
  /** Restrict the list to these service names; omit to show every service. */
  names?: readonly string[];
}

/** Services page: the daemon service list. Rows open the service detail page. */
export function ServicesPage({ names }: ServicesPageProps = {}): ReactNode {
  const t = useOperatorT();
  const selectRows = useCallback<OperatorRowsSelector<ServiceRowData>>(
    (snapshot) => daemonRowsByName(
      snapshot.services.filter(
        (service) => names === undefined || names.includes(service.name),
      ),
    ),
    [names],
  );

  const columns = useMemo<readonly ListColumn<ServiceRowData>[]>(
    () => [
      {
        field: "name",
        header: t("services.column.name"),
        render: (service) => <span className="font-medium text-fg">{service.name}</span>,
      },
      {
        field: "runtime",
        header: t("services.column.runtime"),
        render: (service) => <span className={textRoleVariants({ role: "meta" })}>{service.runtime}</span>,
      },
      {
        field: "status",
        header: t("services.column.status"),
        render: (service) => <StateTag state={service.status} />,
      },
      {
        field: "health",
        header: t("services.column.health"),
        render: (service) => <span className={textRoleVariants({ role: "meta" })}>{service.health ?? "—"}</span>,
      },
    ],
    [t],
  );

  const groupOptions: readonly ResourceToolbarGroupOption[] = useMemo(
    () => [
      { id: "status", label: t("services.column.status"), group: { field: "status" }, type: "value" },
      { id: "runtime", label: t("services.column.runtime"), group: { field: "runtime" }, type: "value" },
    ],
    [t],
  );

  return (
    <OperatorRowsList<ServiceRowData>
      sections={{ services: true }}
      selectRows={selectRows}
      columns={columns}
      groupOptions={groupOptions}
      rowHref={(service) => serviceDetailPath(service.name)}
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

/** Compact single-service row for views that already own the service identity. */
export function ServiceRow({ name, emptyContent }: ServiceRowProps): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const service = (snapshot?.services ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <OperatorSection
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("services.loading")}
      loadingContent={<ServiceRowSkeleton />}
    >
      {service ? (
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
          <RowActions actions={actions} busy={busy} subject={service} />
        </div>
      ) : (
        <p className={cn(textRoleVariants({ role: "meta" }), "border-y border-border-subtle py-3")}>
          {emptyContent ?? t("services.empty")}
        </p>
      )}
    </OperatorSection>
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
