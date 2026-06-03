import { Card, CardContent, CardHeader, CardTitle } from "@angee/base";
import { useT } from "@angee/sdk";
import type { ReactNode } from "react";

import { useOperatorSnapshot } from "../../data/transport";
import { SectionError, SectionLoading } from "../parts/SectionStatus";
import { StateTag } from "../parts/StateTag";

interface MetricTile {
  id: string;
  label: string;
  value: number;
}

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

  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading overview" />;
  }
  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }

  const stack = snapshot?.stack ?? null;
  const health = snapshot?.health ?? null;
  const metrics: readonly MetricTile[] = [
    { id: "services", label: t("section.services.title"), value: snapshot?.services.length ?? 0 },
    { id: "workspaces", label: t("section.workspaces.title"), value: snapshot?.workspaces.length ?? 0 },
    { id: "sources", label: t("section.sources.title"), value: snapshot?.sources.length ?? 0 },
    { id: "secrets", label: t("section.secrets.title"), value: snapshot?.secrets.length ?? 0 },
  ];

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.overview.title")}</h2>

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

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {metrics.map((metric) => (
          <Card key={metric.id}>
            <CardContent className="flex flex-col gap-1 py-4">
              <span className="text-2xl font-semibold tabular-nums text-fg">{metric.value}</span>
              <span className="text-13 text-fg-muted">{metric.label}</span>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
