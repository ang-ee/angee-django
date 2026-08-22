import { type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  Code, ListView, textRoleVariants, useRouteHref, type ResourceToolbarGroupOption, type ListColumn, type RouteHref } from "@angee/ui";

import { usePlatformT } from "../i18n";
import { LinkedChips, TextRouteLink } from "../lib/cells";
import { platformScopeSearch } from "../lib/paths";

// The `platform.Model` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/platform/schema.py`): raw snake fields, fetched + grouped
// client-side by ListView's client row model. The inverse `depended_by` is a
// detail concern (the resource carries only `depends_on`).
interface ModelResourceRow extends Record<string, unknown> {
  id: string;
  label: string;
  model_name: string;
  addon_id: string;
  addon_label: string;
  db_table: string;
  field_count: number;
  relation_count: number;
  resource_type: string | null;
  depends_on: readonly string[];
}

function columns(
  t: (key: string) => string,
  routeHref: RouteHref,
): readonly ListColumn<ModelResourceRow>[] {
  return [
    {
      field: "model_name",
      header: t("col.model"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <TextRouteLink href={routeHref("platform.models.record", { id: row.id })} className="font-medium">
            {row.model_name}
          </TextRouteLink>
          <span className={textRoleVariants({ role: "caption", truncate: true })}>{row.id}</span>
        </span>
      ),
    },
    {
      field: "addon_label",
      header: t("col.addon"),
      render: (row) => (
        <TextRouteLink href={routeHref("platform.addons.record", { id: row.addon_id })}>
          {row.addon_label}
        </TextRouteLink>
      ),
    },
    {
      field: "db_table",
      header: t("col.table"),
      render: (row) => <Code truncate>{row.db_table}</Code>,
    },
    {
      field: "field_count",
      header: t("col.fields"),
      render: (row) => (
        <TextRouteLink
          href={routeHref(
            "platform.fields",
            undefined,
            platformScopeSearch({ model: row.id }),
          )}
        >
          {row.field_count}
        </TextRouteLink>
      ),
    },
    { field: "relation_count", header: t("col.relations") },
    {
      field: "resource_type",
      header: t("col.resourceType"),
      render: (row) => (row.resource_type ? <Code truncate>{row.resource_type}</Code> : null),
    },
    {
      field: "depends_on",
      header: t("col.dependsOn"),
      sortable: false,
      render: (row) => (
        <LinkedChips
          items={row.depends_on}
          href={(id) => routeHref("platform.models.record", { id })}
        />
      ),
    },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "addon_label", label: t("col.addon"), group: { field: "addon_label" }, type: "value" },
    { id: "resource_type", label: t("col.resourceType"), group: { field: "resource_type" }, type: "value" },
  ];
}

export function ModelsPage(): ReactElement {
  const t = usePlatformT();
  const routeHref = useRouteHref();
  const [addonScope] = useQueryState("addon", parseAsString);

  return (
    <ListView<ModelResourceRow>
      resource="platform.Model"
      columns={columns(t, routeHref)}
      groupOptions={groupOptions(t)}
      baseFilter={addonScope ? { addon_id: { exact: addonScope } } : undefined}
      defaultGroup={addonScope ? null : { field: "addon_label" }}
      pageSize={50}
      emptyContent={t("empty.models")}
    />
  );
}
