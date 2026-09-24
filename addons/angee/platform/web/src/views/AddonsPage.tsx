import { type ReactElement } from "react";

import {
  Badge, Chip, ListView, SlotOutlet, useStatusTone, textRoleVariants, useRouteHref, useSlot, type CardActionContext, type ListColumn, type ResourceToolbarGroupOption } from "@angee/ui";

import { usePlatformT } from "../i18n";
import {
  AddonCard,
  AddonCardActions,
  ADDON_MODEL,
  SOURCE_TONES,
  STATE_TONES,
  type AddonResourceRow,
} from "./AddonCard";
import { PLATFORM_ADDON_TOOLBAR_SLOT } from "../slots";

// The name column owns sort/search; label and card-only values are selected
// alongside column fields through the shared resource query.
const CARD_FIELDS = ["label", "description", "keywords", "forced", "pending"] as const;

function columns(t: (key: string) => string, statusTone: ReturnType<typeof useStatusTone>): readonly ListColumn<AddonResourceRow>[] {
  return [
    {
      field: "name",
      header: t("col.addon"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium text-fg">{row.label}</span>
          <span className={textRoleVariants({ role: "caption", truncate: true })}>{row.id}</span>
        </span>
      ),
    },
    {
      field: "category",
      header: t("col.category"),
      render: (row) =>
        row.category ? <Chip tone="muted" size="sm">{row.category}</Chip> : <span className="text-fg-muted">—</span>,
    },
    {
      field: "kind",
      header: t("col.kind"),
      // Route every enum cell through i18n so list and card read the same labels.
      render: (row) => (
        <Badge tone={row.kind === "consumer" ? "brand" : "neutral"}>{t(`kind.${row.kind}`)}</Badge>
      ),
    },
    {
      field: "source",
      header: t("col.source"),
      render: (row) => (
        <Badge tone={statusTone(row.source, SOURCE_TONES, { unknownTone: "neutral" })}>
          {t(`source.${row.source}`)}
        </Badge>
      ),
    },
    {
      field: "state",
      header: t("col.state"),
      render: (row) => <Badge tone={statusTone(row.state, STATE_TONES)}>{t(`state.${row.state}`)}</Badge>,
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
  const statusTone = useStatusTone();
  const t = usePlatformT();
  const routeHref = useRouteHref();
  const toolbarEntries = useSlot(PLATFORM_ADDON_TOOLBAR_SLOT);
  return (
    <ListView<AddonResourceRow>
      resource={ADDON_MODEL}
      columns={columns(t, statusTone)}
      fields={CARD_FIELDS}
      textFilterField="name"
      order={{ name: "ASC" }}
      groupOptions={groupOptions(t)}
      defaultView="board"
      defaultGroup={{ field: "category" }}
      pageSize={100}
      rowHref={(row) => routeHref("platform.addons.record", { id: row.id })}
      toolbarActions={<SlotOutlet entries={toolbarEntries} />}
      renderCard={(row) => <AddonCard row={row} />}
      cardActions={(row: AddonResourceRow, context: CardActionContext) => (
        <AddonCardActions row={row} context={context} />
      )}
      emptyContent={t("empty.addons")}
    />
  );
}
