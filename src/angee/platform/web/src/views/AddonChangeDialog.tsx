import type { ReactElement } from "react";
import { Alert, Chip, Button, DialogForm, ErrorBanner, Glyph, InlineEmpty, LoadingPanel, PaneRow, PaneRows, SectionHeading } from "@angee/ui";

import { usePlatformT } from "../i18n";
import type { AddonChangePreviewData } from "../documents";

export type AddonChangeAction = "ENABLE" | "DISABLE";
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
  const enabling = action === "ENABLE";
  const inventoryModels = preview?.data_inventory.flatMap((inventory) =>
    inventory.models.map((model) => ({ ...model, addon: inventory.addon })),
  ) ?? [];
  const contributedFields = preview?.data_inventory.flatMap((inventory) =>
    inventory.contributed_fields.map((field) => ({ ...field, addon: inventory.addon })),
  ) ?? [];
  return (
    <DialogForm
      open
      onOpenChange={(open) => { if (!open && !applying) onCancel(); }}
      size="lg"
      placement="center"
      title={t(enabling ? "apps.preview.enableTitle" : "apps.preview.disableTitle", { addon: addonLabel })}
      description={t("apps.preview.description")}
      onSubmit={(event) => { event.preventDefault(); onConfirm(); }}
      footer={<>
        <Button type="button" variant="secondary" disabled={applying} onClick={onCancel}>{t("apps.preview.cancel")}</Button>
        <Button
          type="submit"
          variant={enabling ? "primary" : "danger"}
          pending={applying}
          disabled={!preview?.can_apply || loading || applying || Boolean(error)}
        >
          <Glyph decorative name={enabling ? "plus" : "minus"} />
          {t(enabling ? "apps.enable" : "apps.disable")}
        </Button>
      </>}
    >
      {loading && !preview ? <LoadingPanel density="inline" message={t("apps.preview.loading")} /> : null}
      <ErrorBanner description={error} actions={<Button size="sm" variant="secondary" onClick={onRetry}>{t("apps.preview.retry")}</Button>} />
      {preview?.refusal ? <Alert tone="danger">{preview.refusal}</Alert> : null}
      {preview?.migration_warning ? <Alert tone="warning">{preview.migration_warning}</Alert> : null}
      {preview ? (
        <>
          {enabling || preview.addons_to_enable.length ? (
            <PreviewSection title={t("apps.preview.enabled")} count={preview.addons_to_enable.length} empty={t("apps.preview.noDependencyChanges")}>
              {preview.addons_to_enable.map((addon) => <AddonImpactRow key={addon.name} addon={addon} />)}
            </PreviewSection>
          ) : null}
          {!enabling || preview.addons_to_disable.length ? (
            <PreviewSection title={t("apps.preview.disabled")} count={preview.addons_to_disable.length} empty={t("apps.preview.noDependencyChanges")}>
              {preview.addons_to_disable.map((addon) => <AddonImpactRow key={addon.name} addon={addon} />)}
            </PreviewSection>
          ) : null}
          <PreviewSection title={t("apps.preview.dataInventory")} count={inventoryModels.length} empty={t("apps.preview.noDataInventory")}>
            {inventoryModels.map((model) => (
              <li key={`${model.addon}:${model.label}`}>
                <PaneRow
                  icon="grid"
                  title={model.verbose_name}
                  sub={model.label}
                  meta={model.row_count === null ? t("apps.preview.rowCountUnknown") : t("apps.preview.rows", { count: model.row_count })}
                />
              </li>
            ))}
          </PreviewSection>
          {contributedFields.length ? (
            <PreviewSection title={t("apps.preview.contributedFields")} count={contributedFields.length} hint={t("apps.preview.contributedFieldsDescription")} empty={t("apps.preview.noDataInventory")}>
              {contributedFields.map((field) => (
                <li key={`${field.addon}:${field.model_label}:${field.field_name}`}>
                  <PaneRow icon="columns" title={field.verbose_name} sub={`${field.model_label}.${field.field_name}`} meta={field.addon} />
                </li>
              ))}
            </PreviewSection>
          ) : null}
        </>
      ) : null}
    </DialogForm>
  );
}

function AddonImpactRow({ addon }: { addon: AddonChangeImpact }): ReactElement {
  const t = usePlatformT();
  return (
    <li>
      <PaneRow
        icon="list"
        title={addon.label}
        detail={addon.depends_on.length ? t("apps.preview.requires", { addons: addon.depends_on.join(", ") }) : undefined}
        sub={addon.name}
        meta={addon.root
          ? <Chip tone="info">{t("apps.preview.root")}</Chip>
          : <Chip tone="neutral">{t("apps.preview.dependency")}</Chip>}
      />
    </li>
  );
}

function PreviewSection({ title, count, hint, empty, children }: { title: string; count: number; hint?: string; empty: string; children: ReactElement | ReactElement[] }): ReactElement {
  return (
    <div className="grid gap-2">
      <SectionHeading as="h3" label={title} count={count} hint={hint} />
      {count ? <PaneRows>{children}</PaneRows> : <InlineEmpty label={empty} />}
    </div>
  );
}
