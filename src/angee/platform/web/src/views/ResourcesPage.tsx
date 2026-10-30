import type { ReactElement } from "react";

import {
  Code,
  ListView,
  formatDateTime,
  useEnumOptions,
  type FilterClauseField,
  type ListColumn,
  type ResourceToolbarGroupOption,
  type WidgetOption,
} from "@angee/ui";

import { usePlatformT } from "../i18n";

// The `resources.Resource` Hasura resource (`hasura_model_resource` over the
// import ledger, `addons/angee/platform/schema.py`): a real queryset on a
// server row model — list/filter/sort/group resolve server-side via the
// resource's `_groups` aggregate over the source/tier axes.
interface ResourceLedgerResourceRow extends Record<string, unknown> {
  id: string;
  source_addon: string;
  source_path: string;
  tier: string;
  target_model: string;
  target_id: string;
  content_hash: string;
  loaded_at: string;
}

function columns(t: (key: string) => string): readonly ListColumn<ResourceLedgerResourceRow>[] {
  return [
    {
      field: "source_addon",
      header: t("col.source"),
      subline: "source_path",
    },
    { field: "tier", header: t("col.tier") },
    {
      field: "target_model",
      header: t("col.target"),
      subline: "target_id",
      render: (row) => <Code truncate>{row.target_model}</Code>,
    },
    {
      field: "content_hash",
      header: t("col.hash"),
      sortable: false,
      render: (row) => <Code truncate tone="muted">{row.content_hash.slice(0, 12)}</Code>,
    },
    {
      field: "loaded_at",
      header: t("col.loaded"),
      render: (row) => <>{formatDateTime(row.loaded_at)}</>,
    },
  ];
}

function filterFields(t: (key: string) => string, tiers: readonly WidgetOption[]): readonly FilterClauseField[] {
  return [
    {
      id: "source_addon",
      field: "source_addon",
      label: t("col.sourceAddon"),
      type: "text",
    },
    {
      id: "source_path",
      field: "source_path",
      label: t("col.sourcePath"),
      type: "text",
    },
    {
      id: "tier",
      field: "tier",
      label: t("col.tier"),
      type: "selection",
      options: tiers,
    },
  ];
}

function groupOptions(
  t: (key: string) => string,
): readonly ResourceToolbarGroupOption[] {
  return [
    {
      id: "source_addon",
      label: t("col.sourceAddon"),
      group: { field: "source_addon" },
      type: "value",
    },
    {
      id: "source_path",
      label: t("col.sourcePath"),
      group: { field: "source_path" },
      type: "value",
    },
    {
      id: "tier",
      label: t("col.tier"),
      group: { field: "tier" },
      type: "value",
    },
  ];
}

export function ResourcesPage(): ReactElement {
  const t = usePlatformT();
  const tiers = useEnumOptions("resources.Resource", "tier");

  return (
    <ListView<ResourceLedgerResourceRow>
      resource="resources.Resource"
      columns={columns(t)}
      // Render-only reads (see the frontend guidelines' undeclared-field rule).
      fields={["target_id", "source_path"]}
      customFilterFields={filterFields(t, tiers)}
      groupOptions={groupOptions(t)}
      defaultGroup={{ field: "tier" }}
      order={{ source_addon: "ASC" }}
      pageSize={100}
      emptyContent={t("empty.ledger")}
    />
  );
}
