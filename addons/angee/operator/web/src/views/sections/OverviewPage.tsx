import { CommandBand, DetailSection, ErrorBanner, LoadingPanel, MetricStrip, RecordActionBar, RecordHeader, StatusBadge, type ActionDescriptor, type MetricTileValue } from "@angee/ui";
import type { Row } from "@angee/metadata";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorConnection, useOperatorSnapshot } from "../../data/transport";
import { useJobRunOperation } from "../../data/job-run";
import { applicationHealth } from "../../data/application-health";

/** Overview page: the restart verb on the band, stack and health facts, then per-resource count tiles. */
export function OverviewPage(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result } = useOperatorSnapshot({
    overview: true, services: true, workspaces: true, sources: true, secrets: true,
  });
  const connection = useOperatorConnection();
  const jobRun = useJobRunOperation();

  const stack = snapshot?.stack ?? null;
  const services = snapshot?.services ?? [];
  const health = applicationHealth(services);
  const metrics: readonly MetricTileValue[] = [
    { label: t("section.operator.services.title"), value: snapshot?.services.length ?? 0 },
    { label: t("section.operator.workspaces.title"), value: snapshot?.workspaces.length ?? 0 },
    { label: t("section.operator.sources.title"), value: snapshot?.sources.length ?? 0 },
    { label: t("section.operator.secrets.title"), value: snapshot?.secrets.length ?? 0 },
  ];
  const restartJob = connection?.restartJob ?? null;
  const restart = useMemo<readonly ActionDescriptor[]>(() => [{
    id: "restart", label: t("overview.restart"), placement: "toolbar", primary: true,
    disabled: jobRun.active || jobRun.starting,
    disabledReason: restartJob ? undefined : t("overview.restartUnavailable"),
    run: async () => { if (restartJob) await jobRun.run(restartJob, true); },
  }], [jobRun, restartJob, t]);
  const loading = result.fetching && !snapshot;
  const error = result.error ?? jobRun.error;

  return (
    <>
      <CommandBand verbs={<RecordActionBar record={(stack ?? {}) as Row} actions={restart} menuPlacement="leading" />} />
      <div className="flex min-h-0 flex-1 flex-col bg-canvas">
        <RecordHeader density="compact" title={t("section.operator.overview.title")} meta={stack?.name} />
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-3">
          {error ? <ErrorBanner title={t("overview.error")} description={error.message} /> : null}
          {loading ? <LoadingPanel message={t("overview.loading")} /> : (
            <>
              <div className="grid gap-3 sm:grid-cols-2">
                <DetailSection
                  title={t("overview.stack.title")}
                  rows={stack ? [[t("overview.stack.name"), stack.name], [t("overview.stack.root"), stack.root]] : [[t("overview.stack.title"), t("overview.stack.empty")]]}
                />
                <DetailSection
                  title={t("overview.health.title")}
                  rows={health ? [
                    [t("overview.health.status"), <StatusBadge value={health.status} />],
                    ...(health.service ? [[t("overview.health.service"), health.readinessUnavailable
                      ? t("overview.health.readinessUnavailable", { service: health.service })
                      : t("overview.health.serviceState", { service: health.service, state: health.detail ?? "" })] as const] : []),
                  ] : [[t("overview.health.status"), t("overview.health.empty")]]}
                />
              </div>
              <MetricStrip density="prominent" className="grid-cols-2 sm:grid-cols-4" metrics={metrics} />
            </>
          )}
        </div>
      </div>
    </>
  );
}
