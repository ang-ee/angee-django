import { type ReactElement } from "react";

import { NavLink, Chip, Code, DetailSection, DetailSurface, OverflowChipList, useRouteHref, useRouteRecordId } from "@angee/ui";

import { usePlatformT } from "../i18n";
import { platformScopeSearch } from "../lib/paths";
import { usePlatformModel } from "../lib/explorer";

export function ModelDetail(): ReactElement {
  const t = usePlatformT();
  const id = useRouteRecordId();
  const routeHref = useRouteHref();
  const { model, dependedBy, isFetching: fetching } = usePlatformModel(id);

  return (
    <DetailSurface
      publishBreadcrumbLabel
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
            <NavLink href={routeHref("platform.addons.record", { id: model.addon_id })} variant="inline">
              <Chip tone="info">{model.addon_label}</Chip>
            </NavLink>
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
                href: routeHref(
                  "platform.fields",
                  undefined,
                  platformScopeSearch({ model: model.label }),
                ),
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
                href: routeHref("platform.addons.record", { id: model.addon_id }),
              },
              {
                label: t("col.graph"),
                value: t("detail.open"),
                icon: "share",
                href: routeHref(
                  "platform.graph",
                  undefined,
                  platformScopeSearch({ model: model.label }),
                ),
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
                <OverflowChipList items={model.depends_on.map((id) => ({ id, label: id, href: routeHref("platform.models.record", { id }) }))} />,
              ],
              [
                t("col.dependedBy"),
                <OverflowChipList items={dependedBy.map((id) => ({ id, label: id, href: routeHref("platform.models.record", { id }) }))} />,
              ],
            ]}
          />
        </>
      ) : null}
    </DetailSurface>
  );
}
