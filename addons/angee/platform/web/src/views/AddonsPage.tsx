import { type ReactElement } from "react";

import {
  Badge,
  Chip,
  ListView,
  statusTone,
  textRoleVariants,
  type CardActionContext,
  type ListColumn,
  type ResourceToolbarGroupOption,
} from "@angee/ui";

import { usePlatformT } from "../i18n";
import { addonDetailPath } from "../lib/paths";
import {
  AddonCard,
  AddonCardActions,
  ADDON_MODEL,
  SOURCE_TONES,
  STATE_TONES,
  type AddonResourceRow,
} from "./AddonCard";
import { AddonSourceControls } from "./AddonSourceControls";

// Board-card data not shown as a list column: the description/keywords the card
// renders, the forced/pending flags the lifecycle actions branch on, and the VCS
// provenance. Fetched alongside the column fields by the one client row-model query.
const CARD_FIELDS = ["description", "keywords", "forced", "pending", "vcs_path"] as const;

function columns(t: (key: string) => string): readonly ListColumn<AddonResourceRow>[] {
  return [
    {
      field: "label",
      header: t("platform.col.addon"),
      render: (row) => (
        <span className="flex min-w-0 flex-col">
          <span className="truncate font-medium text-fg">{row.label}</span>
          <span className={textRoleVariants({ role: "caption", truncate: true })}>{row.id}</span>
        </span>
      ),
    },
    {
      field: "category",
      header: t("platform.col.category"),
      render: (row) =>
        row.category ? <Chip tone="muted" size="sm">{row.category}</Chip> : <span className="text-fg-muted">—</span>,
    },
    {
      field: "kind",
      header: t("platform.col.kind"),
      // Route every enum cell through i18n so list and card read the same labels.
      render: (row) => (
        <Badge tone={row.kind === "consumer" ? "brand" : "neutral"}>{t(`platform.kind.${row.kind}`)}</Badge>
      ),
    },
    {
      field: "source",
      header: t("platform.col.source"),
      render: (row) => (
        <Badge tone={statusTone(row.source, SOURCE_TONES, { unknownTone: "neutral" })}>
          {t(`platform.source.${row.source}`)}
        </Badge>
      ),
    },
    {
      field: "state",
      header: t("platform.col.state"),
      render: (row) => <Badge tone={statusTone(row.state, STATE_TONES)}>{t(`platform.state.${row.state}`)}</Badge>,
    },
    { field: "model_count", header: t("platform.col.models") },
    { field: "field_count", header: t("platform.col.fields") },
    { field: "resource_count", header: t("platform.col.resources") },
  ];
}

function groupOptions(t: (key: string) => string): readonly ResourceToolbarGroupOption[] {
  return [
    { id: "category", label: t("platform.col.category"), group: { field: "category" }, type: "value" },
    { id: "namespace", label: t("platform.col.namespace"), group: { field: "namespace" }, type: "value" },
    { id: "kind", label: t("platform.col.kind"), group: { field: "kind" }, type: "value" },
    { id: "source", label: t("platform.col.source"), group: { field: "source" }, type: "value" },
    { id: "state", label: t("platform.col.state"), group: { field: "state" }, type: "value" },
  ];
}

/**
 * The Odoo-style Apps board: the `platform.Addon` reflection rendered as category
 * lanes of app cards (board view) over the shared `ListView`, with a list view a
 * toggle away. Cards carry the manifest metadata + lifecycle state and the
 * Install/Uninstall actions; the toolbar grows and rescans the VCS marketplace.
 */
export function AddonsPage(): ReactElement {
  const t = usePlatformT();
  return (
    <ListView<AddonResourceRow>
      resource={ADDON_MODEL}
      columns={columns(t)}
      fields={CARD_FIELDS}
      groupOptions={groupOptions(t)}
      defaultView="board"
      defaultGroup={{ field: "category" }}
      pageSize={100}
      rowHref={(row) => addonDetailPath(row.id)}
      toolbarActions={<AddonSourceControls />}
      renderCard={(row) => <AddonCard row={row} />}
      cardActions={(row: AddonResourceRow, context: CardActionContext) => (
        <AddonCardActions row={row} context={context} />
      )}
      emptyMessage={t("platform.empty.addons")}
    />
  );
}
