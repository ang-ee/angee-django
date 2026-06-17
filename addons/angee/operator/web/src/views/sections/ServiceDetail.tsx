import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Code,
  EmptyState,
  LoadingPanel,
  MetaGrid,
  RecordHeader,
  TextLink,
} from "@angee/base";
import { type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";
import { useQuery } from "urql";

import { SERVICE_ENDPOINT_QUERY } from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { StateTag } from "../parts/StateTag";
import { ServiceLogs } from "./logs";
import { ServiceActions, useServiceActions } from "./service-actions";

interface ServiceEndpointData {
  serviceEndpoint: {
    routed: boolean;
    url: string;
    internalHost: string;
    internalPort: number;
  } | null;
}

/** Service detail: state + lifecycle actions + the live log tail. */
export function ServiceDetail(): ReactElement {
  const t = useOperatorT();
  const params = useParams({ strict: false });
  const name = "name" in params && typeof params.name === "string" ? params.name : undefined;
  const { snapshot, result, refetch } = useOperatorSnapshot({ services: true });
  const { actions, busy } = useServiceActions(refetch);
  const [endpoint] = useQuery<ServiceEndpointData>({
    query: SERVICE_ENDPOINT_QUERY,
    variables: { name: name ?? "" },
    pause: !name,
  });
  const service = (snapshot?.services ?? []).find((candidate) => candidate.name === name) ?? null;
  const resolved = endpoint.data?.serviceEndpoint ?? null;

  if (result.fetching && !snapshot) {
    return <LoadingPanel message={t("operator.services.loading")} />;
  }
  if (!service) {
    return (
      <EmptyState
        fill
        icon="server"
        title={t("operator.services.detail.notFound")}
        description={name}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-4 p-4">
      <RecordHeader
        title={service.name}
        meta={
          <>
            <StateTag state={service.status} />
            <span className="text-fg-muted">{service.runtime}</span>
          </>
        }
      />

      <ServiceActions
        actions={actions}
        busy={busy}
        service={service}
        className="flex flex-wrap gap-1"
      />

      <Card>
        <CardHeader>
          <CardTitle>{t("operator.services.detail.overview")}</CardTitle>
        </CardHeader>
        <CardContent>
          <MetaGrid
            rows={[
              [t("operator.services.column.runtime"), service.runtime],
              [t("operator.services.column.status"), <StateTag state={service.status} />],
              [t("operator.services.column.health"), service.health ?? "—"],
              [
                t("operator.services.detail.endpoint"),
                resolved?.url ? (
                  <TextLink href={resolved.url} target="_blank">{resolved.url}</TextLink>
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
        </CardContent>
      </Card>

      <ServiceLogs name={service.name} />
    </div>
  );
}
