import { Code, DetailSection, DetailSurface, TextLink, useRouteRecordId } from "@angee/ui";
import { type ReactElement } from "react";

import { SERVICE_ENDPOINT_QUERY } from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorQuery, useOperatorSnapshot } from "../../data/transport";
import { StateTag } from "../parts/StateTag";
import { ServiceLogs } from "./logs";
import { useServiceActions } from "./service-actions";
import { OperatorActionButtons } from "../parts/OperatorActionButtons";

/** Service detail: state + lifecycle actions + the live log tail. */
export function ServiceDetail(): ReactElement {
  const t = useOperatorT();
  const name = useRouteRecordId();
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const endpoint = useOperatorQuery(
    SERVICE_ENDPOINT_QUERY,
    { name: name ?? "" },
    { enabled: Boolean(name) },
  );
  const service = (snapshot?.services ?? []).find((candidate) => candidate.name === name) ?? null;
  const resolved = endpoint.data?.serviceEndpoint ?? null;

  return (
    <DetailSurface
      publishBreadcrumbLabel
      loading={result.fetching && !snapshot}
      loadingMessage={t("services.loading")}
      empty={
        !service
          ? {
              icon: "server",
              title: t("services.detail.notFound"),
              description: name,
            }
          : null
      }
      title={service?.name}
      meta={
        service ? (
          <>
            <StateTag state={service.status} />
            <span className="text-fg-muted">{service.runtime}</span>
          </>
        ) : null
      }
      actions={
        service ? (
          <OperatorActionButtons
            actions={actions}
            busy={busy}
            subject={service}
            className="flex flex-wrap gap-1"
          />
        ) : undefined
      }
    >
      {service ? (
        <>
          <DetailSection
            title={t("services.detail.overview")}
            rows={[
              [t("services.column.runtime"), service.runtime],
              [
                t("services.column.status"),
                <StateTag state={service.status} />,
              ],
              [t("services.column.health"), service.health ?? "—"],
              [
                t("services.detail.endpoint"),
                resolved?.url ? (
                  <TextLink href={resolved.url} target="_blank">
                    {resolved.url}
                  </TextLink>
                ) : (
                  "—"
                ),
              ],
              [
                t("services.detail.internal"),
                resolved ? (
                  <Code truncate>{`${resolved.internalHost}:${resolved.internalPort}`}</Code>
                ) : (
                  "—"
                ),
              ],
            ]}
          />

          <ServiceLogs name={service.name} />
        </>
      ) : null}
    </DetailSurface>
  );
}
