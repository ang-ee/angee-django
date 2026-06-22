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
  modelDetailPath,
  modelsPath,
} from "../lib/paths";
import { LinkedChips, useRouteNavigate } from "../lib/cells";
import { usePlatformAddon } from "../lib/explorer";

const shortName = (dep: string): string => dep.split(".").pop() ?? dep;

export function AddonDetail(): ReactElement {
  const t = usePlatformT();
  const params = useParams({ strict: false });
  const id = "id" in params && typeof params.id === "string" ? params.id : undefined;
  const { addon, dependsOn, dependedBy, modelLabels, fetching } =
    usePlatformAddon(id);
  const go = useRouteNavigate();

  if (fetching && !addon) {
    return <LoadingPanel message={t("platform.detail.addon.loading")} />;
  }
  if (!addon) {
    return <EmptyState fill icon="list" title={t("platform.detail.addon.notFound")} description={id} />;
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <RecordHeader
        title={addon.label}
        meta={
          <>
            <Code tone="muted">{addon.id}</Code>
            <Badge tone="neutral">{addon.namespace}</Badge>
            <Badge tone={addon.kind === "required" ? "info" : "neutral"}>{addon.kind}</Badge>
          </>
        }
      />

      <MetricStrip
        metrics={[
          {
            label: t("platform.col.models"),
            value: addon.modelCount,
            icon: "grid",
            href: addon.modelCount ? modelsPath({ addon: addon.id }) : undefined,
            onNavigate: go,
          },
          {
            label: t("platform.col.fields"),
            value: addon.fieldCount,
            icon: "columns",
            href: addon.fieldCount ? fieldsPath({ addon: addon.id }) : undefined,
            onNavigate: go,
          },
          { label: t("platform.col.resources"), value: addon.resourceCount, icon: "files" },
        ]}
      />

      <Card>
        <CardHeader><CardTitle>{t("platform.detail.dependencies")}</CardTitle></CardHeader>
        <CardContent>
          <MetaGrid
            rows={[
              [t("platform.col.dependsOn"), <LinkedChips items={dependsOn} href={addonDetailPath} format={shortName} />],
              [t("platform.col.dependedBy"), <LinkedChips items={dependedBy} href={addonDetailPath} format={shortName} />],
            ]}
          />
        </CardContent>
      </Card>

      {modelLabels.length ? (
        <Card>
          <CardHeader>
            <CardTitle>
              {t("platform.detail.modelsWithCount", { count: modelLabels.length })}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <LinkedChips items={modelLabels} href={modelDetailPath} />
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
