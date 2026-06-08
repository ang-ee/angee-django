import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  MetricGrid,
  type MetricGridTile,
} from "@angee/base";
import { useT } from "@angee/sdk";
import type { ReactNode } from "react";

import { useOperatorSnapshot } from "../../data/transport";
import { OperatorSection } from "../parts/OperatorSection";
import { StateTag } from "../parts/StateTag";

/** Overview pane: stack + health summary above per-resource count tiles. */
export function OverviewSection(): ReactNode {
  const t = useT("operator");
  const { snapshot, result } = useOperatorSnapshot({
    overview: true,
    services: true,
    workspaces: true,
    sources: true,
    secrets: true,
  });

  const stack = snapshot?.stack ?? null;
  const health = snapshot?.health ?? null;
  const metrics: readonly MetricGridTile[] = [
    { label: t("section.operator.services.title"), value: snapshot?.services.length ?? 0 },
    { label: t("section.operator.workspaces.title"), value: snapshot?.workspaces.length ?? 0 },
    { label: t("section.operator.sources.title"), value: snapshot?.sources.length ?? 0 },
    { label: t("section.operator.secrets.title"), value: snapshot?.secrets.length ?? 0 },
  ];

  return (
    <OperatorSection
      title={t("section.operator.overview.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage="Loading overview"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Stack</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-13 text-fg-2">
            {stack ? (
              <>
                <p>{stack.name}</p>
                <p className="text-fg-muted">{stack.root}</p>
              </>
            ) : (
              <p className="text-fg-muted">No stack status.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Health</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-13 text-fg-2">
            {health ? (
              <>
                <StateTag state={health.status} />
                {health.message ? <p>{health.message}</p> : null}
              </>
            ) : (
              <p className="text-fg-muted">No health report.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <MetricGrid className="grid-cols-2 sm:grid-cols-4" metrics={metrics} />
    </OperatorSection>
  );
}
