import {
  Code,
  DetailSection,
  DetailSurface,
  ErrorBanner,
  type MetricTileValue,
} from "@angee/ui";
import type { ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { StateTag } from "../parts/StateTag";

/** Overview page: daemon identity and health in the shared detail surface. */
export function OverviewPage(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result } = useOperatorSnapshot({
    overview: true,
    services: true,
    workspaces: true,
    sources: true,
    secrets: true,
  });

  const stack = snapshot?.stack ?? null;
  const health = snapshot?.health ?? null;
  const metrics: readonly MetricTileValue[] = [
    { label: t("section.operator.services.title"), value: snapshot?.services.length ?? 0 },
    { label: t("section.operator.workspaces.title"), value: snapshot?.workspaces.length ?? 0 },
    { label: t("section.operator.sources.title"), value: snapshot?.sources.length ?? 0 },
    { label: t("section.operator.secrets.title"), value: snapshot?.secrets.length ?? 0 },
  ];

  if (result.error && !snapshot) {
    return (
      <div className="p-4">
        <ErrorBanner description={result.error.message} />
      </div>
    );
  }

  return (
    <DetailSurface
      title={t("section.operator.overview.title")}
      loading={result.fetching && !snapshot}
      loadingMessage={t("operator.overview.loading")}
      metrics={metrics}
      meta={health ? <StateTag state={health.status} /> : null}
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <DetailSection
          title={t("operator.overview.stack.title")}
          rows={[
            [t("operator.overview.stack.name"), stack?.name ?? t("operator.overview.stack.empty")],
            [
              t("operator.overview.stack.root"),
              stack?.root ? <Code truncate>{stack.root}</Code> : "-",
            ],
          ]}
        />

        <DetailSection
          title={t("operator.overview.health.title")}
          rows={[
            [
              t("operator.overview.health.status"),
              health ? <StateTag state={health.status} /> : "-",
            ],
            [
              t("operator.overview.health.message"),
              health?.message ?? t("operator.overview.health.empty"),
            ],
          ]}
        />
      </div>
    </DetailSurface>
  );
}
