import { Code, DetailSection, DetailSurface, TextLink, useAuthoredQuery } from "@angee/ui";
import { type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";

import { SERVICE_ENDPOINT_QUERY } from "../../data/documents.daemon";
import { OPERATOR_PROVIDER } from "../../data/operator-provider";
import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { RowActions } from "../parts/RowActions";
import { StateTag } from "../parts/StateTag";
import { ServiceLogs } from "./logs";
import { useServiceActions } from "./service-actions";

/** Service detail: state + lifecycle actions + the live log tail. */
export function ServiceDetail(): ReactElement {
  const t = useOperatorT();
  const params = useParams({ strict: false });
  const name = "name" in params && typeof params.name === "string" ? params.name : undefined;
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const endpoint = useAuthoredQuery(
    SERVICE_ENDPOINT_QUERY,
    { name: name ?? "" },
    { dataProviderName: OPERATOR_PROVIDER, enabled: Boolean(name) },
  );
  const service = (snapshot?.services ?? []).find((candidate) => candidate.name === name) ?? null;
  const resolved = endpoint.data?.serviceEndpoint ?? null;

  return (
    <DetailSurface
      loading={result.fetching && !snapshot}
      loadingMessage={t("operator.services.loading")}
      empty={
        !service
          ? {
              icon: "server",
              title: t("operator.services.detail.notFound"),
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
          <RowActions
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
            title={t("operator.services.detail.overview")}
            rows={[
              [t("operator.services.column.runtime"), service.runtime],
              [
                t("operator.services.column.status"),
                <StateTag state={service.status} />,
              ],
              [t("operator.services.column.health"), service.health ?? "—"],
              [
                t("operator.services.detail.endpoint"),
                resolved?.url ? (
                  <TextLink href={resolved.url} target="_blank">
                    {resolved.url}
                  </TextLink>
                ) : (
                  "—"
                ),
              ],
              [
                t("operator.services.detail.internal"),
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
