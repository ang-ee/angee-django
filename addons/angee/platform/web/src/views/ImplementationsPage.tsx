import { type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import { Code, ListView, textRoleVariants, useRouteHref, type ListColumn, type ResourceToolbarGroupOption } from "@angee/ui";

import type { PlatformImplementationData } from "../documents";
import { usePlatformT } from "../i18n";
import { TextRouteLink } from "../lib/cells";

type ImplementationRow = Pick<PlatformImplementationData,
  "id" | "label" | "key" | "model" | "field" | "category" | "class_path" | "addon_label" | "addon_id"
> & Record<string, unknown>;

export function ImplementationsPage(): ReactElement {
  const t = usePlatformT();
  const routeHref = useRouteHref();
  const [model] = useQueryState("model", parseAsString);
  const [field] = useQueryState("field", parseAsString);
  const [addon] = useQueryState("addon", parseAsString);
  const columns: readonly ListColumn<ImplementationRow>[] = [
    {
      field: "label", header: t("implementation.name"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <TextRouteLink href={routeHref("platform.implementations.record", { id: row.id })}>
            {row.label}
          </TextRouteLink>
          <span className={textRoleVariants({ role: "caption", truncate: true })}>{row.key}</span>
        </span>
      ),
    },
    { field: "category", header: t("col.category") },
    { field: "model", header: t("col.model") },
    { field: "field", header: t("col.field") },
    {
      field: "addon_label", header: t("col.addon"),
      render: (row) => row.addon_id
        ? <TextRouteLink href={routeHref("platform.addons.record", { id: row.addon_id })}>{row.addon_label}</TextRouteLink>
        : t("implementation.external"),
    },
    { field: "class_path", header: t("implementation.class"), render: (row) => <Code truncate>{row.class_path}</Code> },
  ];
  const groups: readonly ResourceToolbarGroupOption[] = [
    { id: "model", label: t("col.model"), group: { field: "model" }, type: "value" },
    { id: "category", label: t("col.category"), group: { field: "category" }, type: "value" },
    { id: "addon_label", label: t("col.addon"), group: { field: "addon_label" }, type: "value" },
  ];

  return (
    <ListView<ImplementationRow>
      resource="platform.Implementation"
      columns={columns}
      fields={["key", "addon_id"]}
      groupOptions={groups}
      defaultGroup={model ? { field: "category" } : { field: "model" }}
      baseFilter={{
        ...(model ? { model: { exact: model } } : {}),
        ...(field ? { field: { exact: field } } : {}),
        ...(addon ? { addon_id: { exact: addon } } : {}),
      }}
      pageSize={50}
      emptyContent={t("implementation.empty")}
    />
  );
}
