import { type ReactElement } from "react";

import { Chip, ListView, ContainerOutlet, StatusBadge, useRouteHref, useContainer, type CardActionContext, type ListColumn, type ResourceToolbarGroupOption } from "@angee/ui";

import { addonEnumLabel, usePlatformT } from "../i18n";
import {
  AddonCard,
  AddonCardActions,
  ADDON_MODEL,
  type AddonResourceRow,
} from "./AddonCard";

// The name column owns sort/search; label and card-only values are selected
// alongside column fields through the shared resource query.
const CARD_FIELDS = ["label", "description", "keywords", "forced", "pending"] as const;

function columns(t: (key: string) => string): readonly ListColumn<AddonResourceRow>[] {
  return [
    // The label with its id beneath: the shared two-line cell.
    { field: "label", header: t("col.addon"), subline: "name" },
    {
      field: "category",
      header: t("col.category"),
      render: (row) => row.category ? <Chip tone="muted" size="sm">{row.category}</Chip> : null,
    },
    {
      field: "kind",
      header: t("col.kind"),
      // Route every enum cell through i18n so list and card read the same labels.
      render: (row) => <Chip tone="neutral">{addonEnumLabel(t, "kind", row.kind)}</Chip>,
    },
    {
      field: "source",
      header: t("col.source"),
      render: (row) => <StatusBadge value={row.source}>{addonEnumLabel(t, "source", row.source)}</StatusBadge>,
    },
    {
      field: "state",
      header: t("col.state"),
      render: (row) => <StatusBadge value={row.state}>{addonEnumLabel(t, "state", row.state)}</StatusBadge>,
    },
    { field: "model_count", header: t("col.models") },
    { field: "field_count", header: t("col.fields") },
    { field: "resource_count", header: t("col.resources") },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "category", label: t("col.category"), group: { field: "category" }, type: "value" },
    { id: "namespace", label: t("col.namespace"), group: { field: "namespace" }, type: "value" },
    { id: "kind", label: t("col.kind"), group: { field: "kind" }, type: "value" },
    { id: "source", label: t("col.source"), group: { field: "source" }, type: "value" },
    { id: "state", label: t("col.state"), group: { field: "state" }, type: "value" },
  ];
}

/**
 * The Odoo-style Apps board: the `platform.Addon` reflection rendered as category
 * lanes of app cards (board view) over the shared `ListView`, with a list view a
 * toggle away. Cards carry the manifest metadata + lifecycle state and the
 * Install/Disable actions; the toolbar grows and rescans the VCS marketplace.
 */
export function AddonsPage(): ReactElement {
  const t = usePlatformT();
  const routeHref = useRouteHref();
  const toolbarEntries = useContainer("platform.addons#toolbar");
  return (
    <ListView<AddonResourceRow>
      resource={ADDON_MODEL}
      columns={columns(t)}
      fields={CARD_FIELDS}
      textFilterField="name"
      order={{ name: "ASC" }}
      groupOptions={groupOptions(t)}
      defaultView="board"
      defaultGroup={{ field: "category" }}
      pageSize={100}
      rowHref={(row) => routeHref("platform.addons.record", { id: row.id })}
      toolbarActions={<ContainerOutlet entries={toolbarEntries} />}
      renderCard={(row) => <AddonCard row={row} />}
      cardActions={(row: AddonResourceRow, context: CardActionContext) => (
        <AddonCardActions row={row} context={context} />
      )}
      emptyContent={t("empty.addons")}
    />
  );
}
