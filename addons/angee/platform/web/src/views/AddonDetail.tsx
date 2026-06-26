import { type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";

import { Badge, Code, DetailSection, DetailSurface } from "@angee/ui";

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

  return (
    <DetailSurface
      loading={fetching && !addon}
      loadingMessage={t("platform.detail.addon.loading")}
      empty={
        !addon
          ? {
              icon: "list",
              title: t("platform.detail.addon.notFound"),
              description: id,
            }
          : null
      }
      title={addon?.label}
      meta={
        addon ? (
          <>
            <Code tone="muted">{addon.id}</Code>
            <Badge tone="neutral">{addon.namespace}</Badge>
            <Badge tone={addon.kind === "required" ? "info" : "neutral"}>
              {addon.kind}
            </Badge>
          </>
        ) : null
      }
      metrics={
        addon
          ? [
              {
                label: t("platform.col.models"),
                value: addon.model_count,
                icon: "grid",
                href: addon.model_count
                  ? modelsPath({ addon: addon.id })
                  : undefined,
                onNavigate: go,
              },
              {
                label: t("platform.col.fields"),
                value: addon.field_count,
                icon: "columns",
                href: addon.field_count
                  ? fieldsPath({ addon: addon.id })
                  : undefined,
                onNavigate: go,
              },
              {
                label: t("platform.col.resources"),
                value: addon.resource_count,
                icon: "files",
              },
            ]
          : undefined
      }
    >
      {addon ? (
        <>
          <DetailSection
            title={t("platform.detail.dependencies")}
            rows={[
              [
                t("platform.col.dependsOn"),
                <LinkedChips
                  items={dependsOn}
                  href={addonDetailPath}
                  format={shortName}
                />,
              ],
              [
                t("platform.col.dependedBy"),
                <LinkedChips
                  items={dependedBy}
                  href={addonDetailPath}
                  format={shortName}
                />,
              ],
            ]}
          />

          {modelLabels.length ? (
            <DetailSection
              title={t("platform.detail.modelsWithCount", {
                count: modelLabels.length,
              })}
            >
              <LinkedChips items={modelLabels} href={modelDetailPath} />
            </DetailSection>
          ) : null}
        </>
      ) : null}
    </DetailSurface>
  );
}
