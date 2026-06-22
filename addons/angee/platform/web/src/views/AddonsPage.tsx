import { type ReactElement } from "react";

import {
  AuthoredRowsList,
  Badge,
  Code,
  type DataToolbarGroupOption,
  type ListColumn,
} from "@angee/base";
import type { DocumentData } from "@angee/sdk";

import { PlatformExplorer } from "../documents";
import { usePlatformT } from "../i18n";
import { LinkedChips, TextRouteLink } from "../lib/cells";
import { addonDetailPath, fieldsPath, modelsPath } from "../lib/paths";
import { addonRows, type AddonRow } from "../lib/rows";

type PlatformExplorerResult = DocumentData<typeof PlatformExplorer>;

function selectRows(data: PlatformExplorerResult | undefined): readonly AddonRow[] {
  return addonRows(data?.platformExplorer?.addons ?? []);
}

const shortName = (id: string): string => id.split(".").pop() ?? id;

function columns(t: (key: string) => string): readonly ListColumn<AddonRow>[] {
  return [
  {
    field: "addon",
    header: t("platform.col.addon"),
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <TextRouteLink href={addonDetailPath(row.id)} className="font-medium">
          {row.addon}
        </TextRouteLink>
        <span className="truncate text-2xs text-fg-muted">{row.fullName}</span>
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
    field: "models",
    header: t("platform.col.models"),
    render: (row) =>
      row.models ? (
        <TextRouteLink href={modelsPath({ addon: row.id })}>{row.models}</TextRouteLink>
      ) : (
        <span className="text-fg-muted">0</span>
      ),
  },
  {
    field: "fields",
    header: t("platform.col.fields"),
    render: (row) =>
      row.fields ? (
        <TextRouteLink href={fieldsPath({ addon: row.id })}>{row.fields}</TextRouteLink>
      ) : (
        <span className="text-fg-muted">0</span>
      ),
  },
  { field: "resources", header: t("platform.col.resources") },
  {
    field: "dependsOn",
    header: t("platform.col.dependsOn"),
    sortable: false,
    render: (row) => <LinkedChips items={row.dependsOnList} href={addonDetailPath} format={shortName} />,
  },
  {
    field: "dependedBy",
    header: t("platform.col.dependedBy"),
    sortable: false,
    render: (row) => <LinkedChips items={row.dependedByList} href={addonDetailPath} format={shortName} />,
  },
  ];
}

function groupOptions(t: (key: string) => string): readonly DataToolbarGroupOption[] {
  return [
    { id: "namespace", label: t("platform.col.namespace"), group: { field: "namespace" }, type: "value" },
    { id: "kind", label: t("platform.col.kind"), group: { field: "kind" }, type: "value" },
    { id: "dependsOn", label: t("platform.col.dependsOn"), group: { field: "dependsOn" }, type: "value" },
    { id: "dependedBy", label: t("platform.col.dependedBy"), group: { field: "dependedBy" }, type: "value" },
  ];
}

export function AddonsPage(): ReactElement {
  const t = usePlatformT();

  return (
    <AuthoredRowsList
      document={PlatformExplorer}
      selectRows={selectRows}
      columns={columns(t)}
      groupOptions={groupOptions(t)}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
      emptyMessage={t("platform.empty.addons")}
    />
  );
}
