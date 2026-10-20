import { Code, DetailSection, NavLink, useRouteRecordId } from "@angee/ui";
import { type ReactElement } from "react";

import { SERVICE_ENDPOINT_QUERY } from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorQuery, useOperatorSnapshot } from "../../data/transport";
import { DaemonRecord } from "../parts/DaemonRecord";
import { ServiceStatus } from "../parts/ServiceStatus";
import { ServiceLogs } from "./logs";
import { useServiceActions } from "./service-actions";

/** Service record page: band verbs, state and the live log tail. */
export function ServiceDetail(): ReactElement {
  const t = useOperatorT();
  const name = useRouteRecordId();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions } = useServiceActions(refetch);
  const endpoint = useOperatorQuery(SERVICE_ENDPOINT_QUERY, { name: name ?? "" }, { enabled: Boolean(name) });
  const service = (snapshot?.services ?? []).find((candidate) => candidate.name === name) ?? null;
  const resolved = endpoint.data?.serviceEndpoint ?? null;

  return (
    <DaemonRecord
      record={service}
      loading={result.fetching && !snapshot}
      loadingMessage={t("services.loading")}
      notFound={{ icon: "server", title: t("services.detail.notFound"), description: name }}
      eyebrow={t("services.record")}
      title={service?.name}
      meta={service ? <><ServiceStatus service={service} /><span>{service.runtime}</span></> : null}
      verbs={actions}
    >
      {service ? (
        <>
          <DetailSection
            title={t("services.detail.overview")}
            rows={[
              [t("services.column.runtime"), service.runtime],
              [t("services.column.status"), <ServiceStatus service={service} />],
              [t("services.column.health"), service.health],
              [t("services.detail.endpoint"), resolved?.url ? (
                <NavLink href={resolved.url} target="_blank" variant="inline">{resolved.url}</NavLink>
              ) : null],
              [t("services.detail.internal"), resolved ? <Code truncate>{`${resolved.internalHost}:${resolved.internalPort}`}</Code> : null],
            ]}
          />
          <ServiceLogs name={service.name} />
        </>
      ) : null}
    </DaemonRecord>
  );
}
