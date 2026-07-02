import { Card, CardContent, CardHeader, CardTitle, cn, MetricGrid, Skeleton, textRoleVariants, type MetricGridTile } from "@angee/ui";
import type { ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { OperatorSection } from "../parts/OperatorSection";
import { StateTag } from "../parts/StateTag";

/** Overview page: stack + health summary above per-resource count tiles. */
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
      loadingMessage={t("overview.loading")}
      loadingContent={<OverviewLoading />}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>{t("overview.stack.title")}</CardTitle>
          </CardHeader>
          <CardContent className={cn(textRoleVariants({ role: "description" }), "flex flex-col gap-2")}>
            {stack ? (
              <>
                <p>{stack.name}</p>
                <p className="text-fg-muted">{stack.root}</p>
              </>
            ) : (
              <p className="text-fg-muted">{t("overview.stack.empty")}</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>{t("overview.health.title")}</CardTitle>
          </CardHeader>
          <CardContent className={cn(textRoleVariants({ role: "description" }), "flex flex-col gap-2")}>
            {health ? (
              <>
                <StateTag state={health.status} />
                {health.message ? <p>{health.message}</p> : null}
              </>
            ) : (
              <p className="text-fg-muted">{t("overview.health.empty")}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <MetricGrid className="grid-cols-2 sm:grid-cols-4" metrics={metrics} />
    </OperatorSection>
  );
}

function OverviewLoading(): ReactNode {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        {Array.from({ length: 2 }, (_, index) => (
          <Card key={index}>
            <CardHeader>
              <Skeleton shape="text" size="md" className="w-24" />
            </CardHeader>
            <CardContent className="grid gap-2">
              <Skeleton shape="text" size="sm" className="w-2/3" />
              <Skeleton shape="text" size="sm" className="w-5/6" />
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }, (_, index) => (
          <Card key={index} asChild className="px-4 py-3 shadow-none" density="sm">
            <div aria-hidden="true">
              <Skeleton shape="text" size="sm" className="mb-3 w-20" />
              <Skeleton shape="text" size="lg" className="w-10" />
            </div>
          </Card>
        ))}
      </div>
    </>
  );
}
