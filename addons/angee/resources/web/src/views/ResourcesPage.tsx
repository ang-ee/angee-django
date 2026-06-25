import { type ReactElement } from "react";

import {
  Code,
  ListView,
  formatDateTime,
  type ResourceToolbarFilterField,
  type ListColumn,
} from "@angee/base";

import { useResourcesT } from "../i18n";

// The `resources.Resource` Hasura resource (`hasura_model_resource` over the
// import ledger, `addons/angee/resources/schema.py`): a real queryset, so a
// server row model — list/filter/sort resolve server-side. The ledger exposes no
// groupable axis, so the page does not group.
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
      header: t("resources.col.source"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="font-medium text-fg">{row.source_addon}</span>
          <span className="truncate text-2xs text-fg-muted">{row.source_path}</span>
        </span>
      ),
    },
    { field: "tier", header: t("resources.col.tier") },
    {
      field: "target_model",
      header: t("resources.col.target"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <Code truncate>{row.target_model}</Code>
          {row.target_id ? (
            <span className="truncate text-2xs text-fg-muted">{row.target_id}</span>
          ) : null}
        </span>
      ),
    },
    {
      field: "content_hash",
      header: t("resources.col.hash"),
      sortable: false,
      render: (row) => <Code truncate tone="muted">{row.content_hash.slice(0, 12)}</Code>,
    },
    {
      field: "loaded_at",
      header: t("resources.col.loaded"),
      render: (row) => <>{formatDateTime(row.loaded_at)}</>,
    },
  ];
}

function filterFields(t: (key: string) => string): readonly ResourceToolbarFilterField[] {
  return [
    {
      id: "source_addon",
      field: "source_addon",
      label: t("resources.col.sourceAddon"),
      type: "text",
    },
    {
      id: "source_path",
      field: "source_path",
      label: t("resources.col.sourcePath"),
      type: "text",
    },
    {
      id: "tier",
      field: "tier",
      label: t("resources.col.tier"),
      type: "selection",
      options: [
        { value: "master", label: "Master" },
        { value: "install", label: "Install" },
        { value: "demo", label: "Demo" },
      ],
    },
  ];
}

export function ResourcesPage(): ReactElement {
  const t = useResourcesT();

  return (
    <ListView<ResourceLedgerResourceRow>
      resource="resources.Resource"
      columns={columns(t)}
      filterFields={filterFields(t)}
      order={{ source_addon: "ASC" }}
      pageSize={100}
      emptyMessage={t("resources.empty.ledger")}
    />
  );
}
