import { type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";

import { Badge, Code, DetailSection, DetailSurface } from "@angee/ui";

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

  return (
    <DetailSurface
      loading={fetching && !model}
      loadingMessage={t("platform.detail.model.loading")}
      empty={
        !model
          ? {
              icon: "grid",
              title: t("platform.detail.model.notFound"),
              description: id,
            }
          : null
      }
      title={model?.model_name}
      meta={
        model ? (
          <>
            <Code tone="muted">{model.label}</Code>
            <RouterLink href={addonDetailPath(model.addon_id)}>
              <Badge tone="info">{model.addon_label}</Badge>
            </RouterLink>
          </>
        ) : null
      }
      metrics={
        model
          ? [
              {
                label: t("platform.col.fields"),
                value: model.field_count,
                icon: "columns",
                href: fieldsPath({ model: model.label }),
                onNavigate: go,
              },
              {
                label: t("platform.col.relations"),
                value: model.relation_count,
                icon: "share",
              },
              {
                label: t("platform.col.addon"),
                value: model.addon_label,
                icon: "grid",
                href: addonDetailPath(model.addon_id),
                onNavigate: go,
              },
              {
                label: t("platform.col.graph"),
                value: t("platform.detail.open"),
                icon: "share",
                href: graphPath(model.label),
                onNavigate: go,
              },
            ]
          : undefined
      }
    >
      {model ? (
        <>
          <DetailSection
            title={t("platform.detail.definition")}
            rows={[
              [t("platform.col.table"), <Code truncate>{model.db_table}</Code>],
              [t("platform.col.appLabel"), model.app_label],
              ...(model.resource_type
                ? [[
                    t("platform.col.resourceType"),
                    <Code truncate>{model.resource_type}</Code>,
                  ] as const]
                : []),
            ]}
          />

          <DetailSection
            title={t("platform.detail.dependencies")}
            rows={[
              [
                t("platform.col.dependsOn"),
                <LinkedChips items={model.depends_on} href={modelDetailPath} />,
              ],
              [
                t("platform.col.dependedBy"),
                <LinkedChips items={dependedBy} href={modelDetailPath} />,
              ],
            ]}
          />
        </>
      ) : null}
    </DetailSurface>
  );
}
