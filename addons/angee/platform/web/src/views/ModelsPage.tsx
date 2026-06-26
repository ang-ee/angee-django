import { type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  Code,
  ListView,
  type ResourceToolbarGroupOption,
  type ListColumn,
} from "@angee/ui";

import { usePlatformT } from "../i18n";
import { LinkedChips, TextRouteLink } from "../lib/cells";
import { addonDetailPath, fieldsPath, modelDetailPath } from "../lib/paths";

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

function columns(t: (key: string) => string): readonly ListColumn<ModelResourceRow>[] {
  return [
    {
      field: "model_name",
      header: t("platform.col.model"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <TextRouteLink href={modelDetailPath(row.id)} className="font-medium">
            {row.model_name}
          </TextRouteLink>
          <span className="truncate text-2xs text-fg-muted">{row.id}</span>
        </span>
      ),
    },
    {
      field: "addon_label",
      header: t("platform.col.addon"),
      render: (row) => (
        <TextRouteLink href={addonDetailPath(row.addon_id)}>{row.addon_label}</TextRouteLink>
      ),
    },
    {
      field: "db_table",
      header: t("platform.col.table"),
      render: (row) => <Code truncate>{row.db_table}</Code>,
    },
    {
      field: "field_count",
      header: t("platform.col.fields"),
      render: (row) => (
        <TextRouteLink href={fieldsPath({ model: row.id })}>{row.field_count}</TextRouteLink>
      ),
    },
    { field: "relation_count", header: t("platform.col.relations") },
    {
      field: "resource_type",
      header: t("platform.col.resourceType"),
      render: (row) => (row.resource_type ? <Code truncate>{row.resource_type}</Code> : null),
    },
    {
      field: "depends_on",
      header: t("platform.col.dependsOn"),
      sortable: false,
      render: (row) => <LinkedChips items={row.depends_on} href={modelDetailPath} />,
    },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "addon_label", label: t("platform.col.addon"), group: { field: "addon_label" }, type: "value" },
    { id: "resource_type", label: t("platform.col.resourceType"), group: { field: "resource_type" }, type: "value" },
  ];
}

export function ModelsPage(): ReactElement {
  const t = usePlatformT();
  const [addonScope] = useQueryState("addon", parseAsString);

  return (
    <ListView<ModelResourceRow>
      resource="platform.Model"
      columns={columns(t)}
      groupOptions={groupOptions(t)}
      filter={addonScope ? { addon_id: { exact: addonScope } } : undefined}
      defaultGroup={addonScope ? null : { field: "addon_label" }}
      pageSize={50}
      emptyMessage={t("platform.empty.models")}
    />
  );
}
