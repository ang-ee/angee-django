import { type ReactElement } from "react";

import { Badge, Code, DetailSection, DetailSurface, useRouteRecordId } from "@angee/ui";

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
  const id = useRouteRecordId();
  const { model, dependedBy, fetching } = usePlatformModel(id);
  const go = useRouteNavigate();

  return (
    <DetailSurface
      loading={fetching && !model}
      loadingMessage={t("detail.model.loading")}
      empty={
        !model
          ? {
              icon: "grid",
              title: t("detail.model.notFound"),
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
                label: t("col.fields"),
                value: model.field_count,
                icon: "columns",
                href: fieldsPath({ model: model.label }),
                onNavigate: go,
              },
              {
                label: t("col.relations"),
                value: model.relation_count,
                icon: "share",
              },
              {
                label: t("col.addon"),
                value: model.addon_label,
                icon: "grid",
                href: addonDetailPath(model.addon_id),
                onNavigate: go,
              },
              {
                label: t("col.graph"),
                value: t("detail.open"),
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
            title={t("detail.definition")}
            rows={[
              [t("col.table"), <Code truncate>{model.db_table}</Code>],
              [t("col.appLabel"), model.app_label],
              ...(model.resource_type
                ? [[
                    t("col.resourceType"),
                    <Code truncate>{model.resource_type}</Code>,
                  ] as const]
                : []),
            ]}
          />

          <DetailSection
            title={t("detail.dependencies")}
            rows={[
              [
                t("col.dependsOn"),
                <LinkedChips items={model.depends_on} href={modelDetailPath} />,
              ],
              [
                t("col.dependedBy"),
                <LinkedChips items={dependedBy} href={modelDetailPath} />,
              ],
            ]}
          />
        </>
      ) : null}
    </DetailSurface>
  );
}
