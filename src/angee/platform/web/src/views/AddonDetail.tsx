import { type ReactElement } from "react";

import { Chip, Code, DetailSection, DetailSurface, OverflowChipList, useRouteHref, useRouteRecordId } from "@angee/ui";

import { addonEnumLabel, usePlatformT } from "../i18n";
import { platformScopeSearch } from "../lib/paths";
import { usePlatformAddon, usePlatformAddonHref } from "../lib/explorer";

export function AddonDetail(): ReactElement {
  const t = usePlatformT();
  const id = useRouteRecordId();
  const routeHref = useRouteHref();
  const addonHref = usePlatformAddonHref();
  const { addon, dependsOn, dependedBy, modelLabels, isFetching: fetching } =
    usePlatformAddon(id);

  return (
    <DetailSurface
      publishBreadcrumbLabel
      loading={fetching && !addon}
      loadingMessage={t("detail.addon.loading")}
      empty={
        !addon
          ? {
              icon: "list",
              title: t("detail.addon.notFound"),
              description: id,
            }
          : null
      }
      title={addon?.label}
      meta={
        addon ? (
          <>
            <Code tone="muted">{addon.name}</Code>
            <Chip tone="muted">{addon.installed_version
              ? t("apps.installedVersion", { version: addon.installed_version })
              : t("apps.notInstalled")}</Chip>
            <Chip tone="neutral">{addon.namespace}</Chip>
            <Chip tone="neutral">{addonEnumLabel(t, "kind", addon.kind)}</Chip>
          </>
        ) : null
      }
      metrics={
        addon
          ? [
              {
                label: t("col.models"),
                value: addon.model_count,
                icon: "grid",
                href: addon.model_count
                  ? routeHref(
                      "platform.models",
                      undefined,
                      platformScopeSearch({ addon: addon.name }),
                    )
                  : undefined,
              },
              {
                label: t("col.fields"),
                value: addon.field_count,
                icon: "columns",
                href: addon.field_count
                  ? routeHref(
                      "platform.fields",
                      undefined,
                      platformScopeSearch({ addon: addon.name }),
                    )
                  : undefined,
              },
              {
                label: t("col.resources"),
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
            title={t("detail.dependencies")}
            rows={[
              [
                t("col.dependsOn"),
                <OverflowChipList items={dependsOn.map((id) => ({ id, label: id, href: addonHref(id) }))} />,
              ],
              [
                t("col.dependedBy"),
                <OverflowChipList items={dependedBy.map((id) => ({ id, label: id, href: addonHref(id) }))} />,
              ],
            ]}
          />

          {modelLabels.length ? (
            <DetailSection
              title={t("detail.modelsWithCount", {
                count: modelLabels.length,
              })}
            >
              <OverflowChipList items={modelLabels.map((id) => ({ id, label: id, href: routeHref("platform.models.record", { id }) }))} />
            </DetailSection>
          ) : null}
        </>
      ) : null}
    </DetailSurface>
  );
}
