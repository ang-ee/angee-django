import { type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";

import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Code,
  EmptyState,
  LoadingPanel,
  MetaGrid,
  MetricStrip,
  RecordHeader,
} from "@angee/base";

import { usePlatformT } from "../i18n";
import {
  addonDetailPath,
  fieldsPath,
  graphPath,
  modelDetailPath,
} from "../lib/paths";
import { LinkedChips, RouterLink, useRouteNavigate } from "../lib/cells";
import { usePlatformModel } from "../lib/explorer";

export function ModelDetail(): ReactElement {
  const t = usePlatformT();
  const params = useParams({ strict: false });
  const id = "id" in params && typeof params.id === "string" ? params.id : undefined;
  const { model, dependedBy, fetching } = usePlatformModel(id);
  const go = useRouteNavigate();

  if (fetching && !model) {
    return <LoadingPanel message={t("platform.detail.model.loading")} />;
  }
  if (!model) {
    return <EmptyState fill icon="grid" title={t("platform.detail.model.notFound")} description={id} />;
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <RecordHeader
        title={model.modelName}
        meta={
          <>
            <Code tone="muted">{model.label}</Code>
            <RouterLink href={addonDetailPath(model.addonId)}>
              <Badge tone="info">{model.addonLabel}</Badge>
            </RouterLink>
          </>
        }
      />

      <MetricStrip
        metrics={[
          { label: t("platform.col.fields"), value: model.fieldCount, icon: "columns", href: fieldsPath({ model: model.label }), onNavigate: go },
          { label: t("platform.col.relations"), value: model.relationCount, icon: "share" },
          { label: t("platform.col.addon"), value: model.addonLabel, icon: "grid", href: addonDetailPath(model.addonId), onNavigate: go },
          { label: t("platform.col.graph"), value: t("platform.detail.open"), icon: "share", href: graphPath(model.label), onNavigate: go },
        ]}
      />

      <Card>
        <CardHeader><CardTitle>{t("platform.detail.definition")}</CardTitle></CardHeader>
        <CardContent>
          <MetaGrid
            rows={[
              [t("platform.col.table"), <Code truncate>{model.dbTable}</Code>],
              [t("platform.col.appLabel"), model.appLabel],
              ...(model.resourceType
                ? [[t("platform.col.resourceType"), <Code truncate>{model.resourceType}</Code>] as const]
                : []),
            ]}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("platform.detail.dependencies")}</CardTitle></CardHeader>
        <CardContent>
          <MetaGrid
            rows={[
              [t("platform.col.dependsOn"), <LinkedChips items={model.dependsOn} href={modelDetailPath} />],
              [t("platform.col.dependedBy"), <LinkedChips items={dependedBy} href={modelDetailPath} />],
            ]}
          />
        </CardContent>
      </Card>
    </div>
  );
}
