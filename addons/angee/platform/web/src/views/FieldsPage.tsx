import { type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  ListView,
  type ResourceToolbarGroupOption,
  type ListColumn,
} from "@angee/ui";

import { usePlatformT } from "../i18n";
import { TextRouteLink } from "../lib/cells";
import { addonDetailPath, modelDetailPath } from "../lib/paths";

// The `platform.Field` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/platform/schema.py`): every composed model's fields flattened
// into one collection, fetched + grouped client-side by ListView's client row
// model. `model`/`addon` carry the owning context.
interface FieldResourceRow extends Record<string, unknown> {
  id: string;
  name: string;
  kind: string;
  relation_target: string | null;
  model: string;
  addon: string;
}

function columns(t: (key: string) => string): readonly ListColumn<FieldResourceRow>[] {
  return [
    {
      field: "name",
      header: t("platform.col.field"),
      render: (row) => <span className="font-medium text-fg">{row.name}</span>,
    },
    {
      field: "model",
      header: t("platform.col.model"),
      render: (row) => (
        <TextRouteLink href={modelDetailPath(row.model)}>{row.model}</TextRouteLink>
      ),
    },
    {
      field: "addon",
      header: t("platform.col.addon"),
      render: (row) => (
        <TextRouteLink href={addonDetailPath(row.addon)}>{row.addon}</TextRouteLink>
      ),
    },
    { field: "kind", header: t("platform.col.type") },
    {
      field: "relation_target",
      header: t("platform.col.relationTarget"),
      render: (row) =>
        row.relation_target ? (
          <TextRouteLink href={modelDetailPath(row.relation_target)}>
            {row.relation_target}
          </TextRouteLink>
        ) : null,
    },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "addon", label: t("platform.col.addon"), group: { field: "addon" }, type: "value" },
    { id: "model", label: t("platform.col.model"), group: { field: "model" }, type: "value" },
    { id: "kind", label: t("platform.col.type"), group: { field: "kind" }, type: "value" },
    { id: "relation_target", label: t("platform.col.relationTarget"), group: { field: "relation_target" }, type: "value" },
  ];
}

export function FieldsPage(): ReactElement {
  const t = usePlatformT();
  const [modelScope] = useQueryState("model", parseAsString);
  const [addonScope] = useQueryState("addon", parseAsString);

  // Both scope axes are distinct exact-match fields, so the base filter merges
  // them into one object; ListView ANDs it with the user-owned view filter.
  const filter = {
    ...(modelScope ? { model: { exact: modelScope } } : {}),
    ...(addonScope ? { addon: { exact: addonScope } } : {}),
  };

  return (
    <ListView<FieldResourceRow>
      resource="platform.Field"
      columns={columns(t)}
      groupOptions={groupOptions(t)}
      filter={Object.keys(filter).length > 0 ? filter : undefined}
      defaultGroup={modelScope ? null : { field: "model" }}
      pageSize={100}
      emptyMessage={t("platform.empty.fields")}
    />
  );
}
