import type { ReactElement } from "react";
import { Alert, Badge, Button, Dialog, Glyph, LoadingPanel } from "@angee/ui";

import { usePlatformT } from "../i18n";
import type { AddonChangePreviewData } from "../documents";

export type AddonChangeAction = "INSTALL" | "DISABLE";
type AddonChangeImpact = AddonChangePreviewData["addons_to_enable"][number];

export interface AddonChangeDialogProps {
  action: AddonChangeAction;
  addonLabel: string;
  preview: AddonChangePreviewData | null;
  loading: boolean;
  applying: boolean;
  error: string | null;
  onRetry: () => void;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Preview one server-owned addon graph change before applying its exact revision. */
export function AddonChangeDialog({
  action,
  addonLabel,
  preview,
  loading,
  applying,
  error,
  onRetry,
  onConfirm,
  onCancel,
}: AddonChangeDialogProps): ReactElement {
  const t = usePlatformT();
  const installing = action === "INSTALL";
  const inventoryModels = preview?.data_inventory.flatMap((inventory) =>
    inventory.models.map((model) => ({ ...model, addon: inventory.addon })),
  ) ?? [];
  const contributedFields = preview?.data_inventory.flatMap((inventory) =>
    inventory.contributed_fields.map((field) => ({ ...field, addon: inventory.addon })),
  ) ?? [];
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !applying) onCancel(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop />
        <Dialog.Content size="lg" placement="center">
          <Dialog.Header>
            <Dialog.Title>{t(installing ? "apps.preview.installTitle" : "apps.preview.disableTitle", { addon: addonLabel })}</Dialog.Title>
            <Dialog.Description>{t("apps.preview.description")}</Dialog.Description>
          </Dialog.Header>
          <Dialog.Body className="space-y-4">
            {loading && !preview ? <LoadingPanel density="inline" message={t("apps.preview.loading")} /> : null}
            {error ? (
              <Alert tone="danger">
                <div className="flex items-center justify-between gap-3">
                  <span>{error}</span>
                  <Button size="sm" variant="secondary" onClick={onRetry}>{t("apps.preview.retry")}</Button>
                </div>
              </Alert>
            ) : null}
            {preview?.refusal ? <Alert tone="danger">{preview.refusal}</Alert> : null}
            {preview?.migration_warning ? <Alert tone="warning">{preview.migration_warning}</Alert> : null}
            {preview ? (
              <>
                {installing || preview.addons_to_enable.length ? (
                  <PreviewSection title={t("apps.preview.enabled")} empty={t("apps.preview.noDependencyChanges")}>
                    {preview.addons_to_enable.map((addon) => <AddonImpactItem key={addon.name} addon={addon} />)}
                  </PreviewSection>
                ) : null}
                {!installing || preview.addons_to_disable.length ? (
                  <PreviewSection title={t("apps.preview.disabled")} empty={t("apps.preview.noDependencyChanges")}>
                    {preview.addons_to_disable.map((addon) => <AddonImpactItem key={addon.name} addon={addon} />)}
                  </PreviewSection>
                ) : null}
                <PreviewSection title={t("apps.preview.dataInventory")} empty={t("apps.preview.noDataInventory")}>
                    {inventoryModels.map((model) => (
                        <li key={`${model.addon}:${model.label}`} className="flex items-baseline justify-between gap-3 rounded-6 border border-border-subtle px-3 py-2">
                          <span className="min-w-0">
                            <span className="block truncate text-fg">{model.verbose_name}</span>
                            <span className="block truncate text-12 text-fg-muted">{model.label}</span>
                          </span>
                          <span className="shrink-0 text-12 text-fg-muted">
                            {model.row_count === null ? t("apps.preview.rowCountUnknown") : t("apps.preview.rows", { count: model.row_count })}
                          </span>
                        </li>
                    ))}
                </PreviewSection>
                {contributedFields.length ? (
                  <section className="space-y-2">
                    <h3 className="text-13 font-semibold text-fg">{t("apps.preview.contributedFields")}</h3>
                    <p className="text-13 text-fg-muted">{t("apps.preview.contributedFieldsDescription")}</p>
                    <ul className="space-y-2">
                      {contributedFields.map((field) => (
                        <li key={`${field.addon}:${field.model_label}:${field.field_name}`} className="rounded-6 border border-border-subtle px-3 py-2">
                          <span className="block text-fg">{field.verbose_name}</span>
                          <span className="block break-all text-12 text-fg-muted">{field.model_label}.{field.field_name}</span>
                          <span className="block text-12 text-fg-muted">{field.addon}</span>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
              </>
            ) : null}
          </Dialog.Body>
          <Dialog.Footer>
            <Button type="button" variant="secondary" disabled={applying} onClick={onCancel}>{t("apps.preview.cancel")}</Button>
            <Button
              type="button"
              variant={installing ? "primary" : "danger"}
              pending={applying}
              disabled={!preview?.can_apply || loading || applying || Boolean(error)}
              onClick={onConfirm}
            >
              <Glyph decorative name={installing ? "plus" : "minus"} />
              {t(installing ? "apps.install" : "apps.disable")}
            </Button>
          </Dialog.Footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function AddonImpactItem({ addon }: { addon: AddonChangeImpact }): ReactElement {
  const t = usePlatformT();
  return (
    <li className="flex items-center justify-between gap-2 rounded-6 border border-border-subtle px-3 py-2">
      <span className="min-w-0">
        <span className="block truncate font-medium text-fg">{addon.label}</span>
        <span className="block truncate text-12 text-fg-muted">{addon.name}</span>
        {addon.depends_on.length ? (
          <span className="block truncate text-12 text-fg-muted">
            {t("apps.preview.requires", { addons: addon.depends_on.join(", ") })}
          </span>
        ) : null}
      </span>
      {addon.root ? (
        <Badge tone="info">{t("apps.preview.root")}</Badge>
      ) : (
        <Badge tone="neutral">{t("apps.preview.dependency")}</Badge>
      )}
    </li>
  );
}

function PreviewSection({ title, empty, children }: { title: string; empty: string; children: ReactElement | ReactElement[] }): ReactElement {
  const items = Array.isArray(children) ? children : [children];
  return (
    <section className="space-y-2">
      <h3 className="text-13 font-semibold text-fg">{title}</h3>
      {items.length ? <ul className="space-y-2">{items}</ul> : <p className="text-13 text-fg-muted">{empty}</p>}
    </section>
  );
}
