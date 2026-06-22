import { useCallback, type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  AuthoredRowsList,
  Code,
  type DataToolbarGroupOption,
  type ListColumn,
} from "@angee/base";
import type { DocumentData } from "@angee/sdk";

import { PlatformExplorer } from "../documents";
import { usePlatformT } from "../i18n";
import { LinkedChips, TextRouteLink } from "../lib/cells";
import { addonDetailPath, fieldsPath, modelDetailPath } from "../lib/paths";
import { modelRows, type ModelRow } from "../lib/rows";

type PlatformExplorerResult = DocumentData<typeof PlatformExplorer>;

function columns(t: (key: string) => string): readonly ListColumn<ModelRow>[] {
  return [
  {
    field: "model",
    header: t("platform.col.model"),
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <TextRouteLink href={modelDetailPath(row.id)} className="font-medium">
          {row.model}
        </TextRouteLink>
        <span className="truncate text-2xs text-fg-muted">{row.id}</span>
      </span>
    ),
  },
  {
    field: "addon",
    header: t("platform.col.addon"),
    render: (row) => (
      <TextRouteLink href={addonDetailPath(row.addonId)}>{row.addon}</TextRouteLink>
    ),
  },
  { field: "table", header: t("platform.col.table"), render: (row) => <Code truncate>{row.table}</Code> },
  {
    field: "fields",
    header: t("platform.col.fields"),
    render: (row) => (
      <TextRouteLink href={fieldsPath({ model: row.id })}>{row.fields}</TextRouteLink>
    ),
  },
  { field: "relations", header: t("platform.col.relations") },
  {
    field: "resourceType",
    header: t("platform.col.resourceType"),
    render: (row) => (row.resourceType ? <Code truncate>{row.resourceType}</Code> : null),
  },
  {
    field: "dependsOn",
    header: t("platform.col.dependsOn"),
    sortable: false,
    render: (row) => <LinkedChips items={row.dependsOnList} href={modelDetailPath} />,
  },
  {
    field: "dependedBy",
    header: t("platform.col.dependedBy"),
    sortable: false,
    render: (row) => <LinkedChips items={row.dependedByList} href={modelDetailPath} />,
  },
  ];
}

function groupOptions(t: (key: string) => string): readonly DataToolbarGroupOption[] {
  return [
    { id: "addon", label: t("platform.col.addon"), group: { field: "addon" }, type: "value" },
    { id: "dependsOn", label: t("platform.col.dependsOn"), group: { field: "dependsOn" }, type: "value" },
    { id: "dependedBy", label: t("platform.col.dependedBy"), group: { field: "dependedBy" }, type: "value" },
  ];
}

export function ModelsPage(): ReactElement {
  const t = usePlatformT();
  const [addonScope] = useQueryState("addon", parseAsString);
  const selectRows = useCallback((data: PlatformExplorerResult | undefined) => {
    const all = modelRows(data?.platformExplorer?.models ?? []);
    return addonScope ? all.filter((row) => row.addonId === addonScope) : all;
  }, [addonScope]);

  return (
    <AuthoredRowsList
      document={PlatformExplorer}
      selectRows={selectRows}
      columns={columns(t)}
      groupOptions={groupOptions(t)}
      defaultGroup={{ field: "addon" }}
      pageSize={50}
      emptyMessage={t("platform.empty.models")}
    />
  );
}
