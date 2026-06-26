import { type ReactElement } from "react";

import {
  Badge,
  Code,
  ListView,
  type ListColumn,
  type ResourceToolbarGroupOption,
} from "@angee/ui";

import { usePlatformT } from "../i18n";
import { LinkedChips, TextRouteLink } from "../lib/cells";
import { addonDetailPath, fieldsPath, modelsPath } from "../lib/paths";

// The `platform.Addon` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/platform/schema.py`): raw snake fields, fetched + grouped
// client-side by ListView's client row model. `depends_on`/`depended_by` arrive
// already resolved across the whole addon set (the server inverts `depends_on`).
interface AddonResourceRow extends Record<string, unknown> {
  id: string;
  label: string;
  namespace: string;
  kind: string;
  model_count: number;
  field_count: number;
  resource_count: number;
  depends_on: readonly string[];
  depended_by: readonly string[];
}

const shortName = (id: string): string => id.split(".").pop() ?? id;

function columns(t: (key: string) => string): readonly ListColumn<AddonResourceRow>[] {
  return [
    {
      field: "label",
      header: t("platform.col.addon"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <TextRouteLink href={addonDetailPath(row.id)} className="font-medium">
            {row.label}
          </TextRouteLink>
          <span className="truncate text-2xs text-fg-muted">{row.id}</span>
        </span>
      ),
    },
    {
      field: "namespace",
      header: t("platform.col.namespace"),
      render: (row) => <Code truncate>{row.namespace}</Code>,
    },
    {
      field: "kind",
      header: t("platform.col.kind"),
      render: (row) => (
        <Badge tone={row.kind === "required" ? "info" : "neutral"}>{row.kind}</Badge>
      ),
    },
    {
      field: "model_count",
      header: t("platform.col.models"),
      render: (row) =>
        row.model_count ? (
          <TextRouteLink href={modelsPath({ addon: row.id })}>{row.model_count}</TextRouteLink>
        ) : (
          <span className="text-fg-muted">0</span>
        ),
    },
    {
      field: "field_count",
      header: t("platform.col.fields"),
      render: (row) =>
        row.field_count ? (
          <TextRouteLink href={fieldsPath({ addon: row.id })}>{row.field_count}</TextRouteLink>
        ) : (
          <span className="text-fg-muted">0</span>
        ),
    },
    { field: "resource_count", header: t("platform.col.resources") },
    {
      field: "depends_on",
      header: t("platform.col.dependsOn"),
      sortable: false,
      render: (row) => (
        <LinkedChips items={row.depends_on} href={addonDetailPath} format={shortName} />
      ),
    },
    {
      field: "depended_by",
      header: t("platform.col.dependedBy"),
      sortable: false,
      render: (row) => (
        <LinkedChips items={row.depended_by} href={addonDetailPath} format={shortName} />
      ),
    },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "namespace", label: t("platform.col.namespace"), group: { field: "namespace" }, type: "value" },
    { id: "kind", label: t("platform.col.kind"), group: { field: "kind" }, type: "value" },
  ];
}

export function AddonsPage(): ReactElement {
  const t = usePlatformT();

  return (
    <ListView<AddonResourceRow>
      resource="platform.Addon"
      columns={columns(t)}
      groupOptions={groupOptions(t)}
      defaultGroup={{ field: "namespace" }}
      pageSize={100}
      emptyMessage={t("platform.empty.addons")}
    />
  );
}
