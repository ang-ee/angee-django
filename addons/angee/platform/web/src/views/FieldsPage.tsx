import { type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  TextLink,
  ListView, useRouteHref, type ListColumn, type RouteHref } from "@angee/ui";

import { usePlatformT } from "../i18n";

// The `platform.Field` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/platform/schema.py`): every composed model's fields flattened
// into one server-paged collection. `model`/`addon` carry the owning context;
// the resource exposes filtering and sorting, without grouping axes.
interface FieldResourceRow extends Record<string, unknown> {
  id: string;
  name: string;
  kind: string;
  relation_target: string | null;
  model: string;
  addon: string;
}

function columns(
  t: (key: string) => string,
  routeHref: RouteHref,
): readonly ListColumn<FieldResourceRow>[] {
  return [
    {
      field: "name",
      header: t("col.field"),
      render: (row) => <span className="font-medium text-fg">{row.name}</span>,
    },
    {
      field: "model",
      header: t("col.model"),
      render: (row) => (
        <TextLink href={routeHref("platform.models.record", { id: row.model })}>
          {row.model}
        </TextLink>
      ),
    },
    {
      field: "addon",
      header: t("col.addon"),
      render: (row) => (
        <TextLink href={routeHref("platform.addons.record", { id: row.addon })}>
          {row.addon}
        </TextLink>
      ),
    },
    { field: "kind", header: t("col.type") },
    {
      field: "relation_target",
      header: t("col.relationTarget"),
      render: (row) =>
        row.relation_target ? (
          <TextLink href={routeHref("platform.models.record", { id: row.relation_target })}>
            {row.relation_target}
          </TextLink>
        ) : null,
    },
  ];
}

export function FieldsPage(): ReactElement {
  const t = usePlatformT();
  const routeHref = useRouteHref();
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
      columns={columns(t, routeHref)}
      baseFilter={Object.keys(filter).length > 0 ? filter : undefined}
      pageSize={100}
      emptyContent={t("empty.fields")}
    />
  );
}
