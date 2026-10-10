import { type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  NavLink,
  ListView, useRouteHref, type ResourceToolbarGroupOption, type ListColumn, type RouteHref } from "@angee/ui";

import { usePlatformT } from "../i18n";

// The `platform.Field` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/platform/schema.py`): every composed model's fields flattened
// into one server-paged collection, grouped through the resource's declared
// group columns. `model`/`addon` carry the owning context.
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
        <NavLink href={routeHref("platform.models.record", { id: row.model })} variant="inline">
          {row.model}
        </NavLink>
      ),
    },
    {
      field: "addon",
      header: t("col.addon"),
      render: (row) => (
        <NavLink href={routeHref("platform.addons.record", { id: row.addon })} variant="inline">
          {row.addon}
        </NavLink>
      ),
    },
    { field: "kind", header: t("col.type") },
    {
      field: "relation_target",
      header: t("col.relationTarget"),
      render: (row) =>
        row.relation_target ? (
          <NavLink href={routeHref("platform.models.record", { id: row.relation_target })} variant="inline">
            {row.relation_target}
          </NavLink>
        ) : null,
    },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "addon", label: t("col.addon"), group: { field: "addon" }, type: "value" },
    { id: "model", label: t("col.model"), group: { field: "model" }, type: "value" },
    { id: "kind", label: t("col.type"), group: { field: "kind" }, type: "value" },
    { id: "relation_target", label: t("col.relationTarget"), group: { field: "relation_target" }, type: "value" },
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
      groupOptions={groupOptions(t)}
      baseFilter={Object.keys(filter).length > 0 ? filter : undefined}
      // A model-scoped list is already one model; grouping by it adds nothing.
      defaultGroup={modelScope ? null : { field: "model" }}
      pageSize={100}
      emptyContent={t("empty.fields")}
    />
  );
}
