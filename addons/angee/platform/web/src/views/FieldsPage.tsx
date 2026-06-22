import { useCallback, type ReactElement } from "react";
import { parseAsString, useQueryState } from "nuqs";

import {
  AuthoredRowsList,
  type DataToolbarGroupOption,
  type ListColumn,
} from "@angee/base";
import type { DocumentData } from "@angee/sdk";

import { PlatformExplorer } from "../documents";
import { usePlatformT } from "../i18n";
import { TextRouteLink } from "../lib/cells";
import { addonDetailPath, modelDetailPath } from "../lib/paths";
import { fieldRows, type FieldRow } from "../lib/rows";

type PlatformExplorerResult = DocumentData<typeof PlatformExplorer>;

function columns(t: (key: string) => string): readonly ListColumn<FieldRow>[] {
  return [
  {
    field: "field",
    header: t("platform.col.field"),
    render: (row) => <span className="font-medium text-fg">{row.field}</span>,
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
      <TextRouteLink href={addonDetailPath(row.addonId)}>{row.addon}</TextRouteLink>
    ),
  },
  { field: "kind", header: t("platform.col.type") },
  {
    field: "relationTarget",
    header: t("platform.col.relationTarget"),
    render: (row) =>
      row.relationTarget ? (
        <TextRouteLink href={modelDetailPath(row.relationTarget)}>
          {row.relationTarget}
        </TextRouteLink>
      ) : null,
  },
  ];
}

function groupOptions(t: (key: string) => string): readonly DataToolbarGroupOption[] {
  return [
    { id: "addon", label: t("platform.col.addon"), group: { field: "addon" }, type: "value" },
    { id: "model", label: t("platform.col.model"), group: { field: "model" }, type: "value" },
    { id: "kind", label: t("platform.col.type"), group: { field: "kind" }, type: "value" },
    { id: "relationTarget", label: t("platform.col.relationTarget"), group: { field: "relationTarget" }, type: "value" },
  ];
}

export function FieldsPage(): ReactElement {
  const t = usePlatformT();
  const [modelScope] = useQueryState("model", parseAsString);
  const [addonScope] = useQueryState("addon", parseAsString);
  const selectRows = useCallback((data: PlatformExplorerResult | undefined) => {
    let all = fieldRows(data?.platformExplorer?.models ?? []);
    if (modelScope) all = all.filter((row) => row.model === modelScope);
    if (addonScope) all = all.filter((row) => row.addonId === addonScope);
    return all;
  }, [modelScope, addonScope]);

  return (
    <AuthoredRowsList
      document={PlatformExplorer}
      selectRows={selectRows}
      columns={columns(t)}
      groupOptions={groupOptions(t)}
      defaultGroup={modelScope ? null : { field: "model" }}
      pageSize={100}
      emptyMessage={t("platform.empty.fields")}
    />
  );
}
