import { useCallback, useRef, useState, type ReactElement, type ReactNode } from "react";
import { Chip, Button, Glyph, OverflowChipList, StatusBadge, errorMessage, textRoleVariants, useActionResultRun, useAuthoredResourceMutation, type CardActionContext } from "@angee/ui";
import { useAuthoredQuery } from "@angee/refine";
import type { AddonKind } from "@angee/gql/console/graphql";

import {
  AddonChangePreview,
  DisableAddon,
  EnableAddon,
  InstallAddon,
  UninstallAddon,
  PLATFORM_ADDON_MUTATION_INVALIDATES,
} from "../documents";
import { addonEnumLabel, usePlatformT } from "../i18n";
import { AddonChangeDialog, type AddonChangeAction } from "./AddonChangeDialog";

/** The reflection resource the board reads + invalidates after every lifecycle write. */
export const ADDON_MODEL = "platform.Addon";

// The `platform.Addon` Hasura resource row (`hasura_model_resource` over the
// system-synced reflection table). Raw
// snake fields, fetched + grouped client-side by the board's client row model.
export interface AddonResourceRow extends Record<string, unknown> {
  id: string;
  name: string;
  label: string;
  namespace: string;
  category: string;
  description: string;
  keywords: readonly string[];
  kind: AddonKind;
  source: string;
  state: string;
  forced: boolean;
  pending: boolean;
  model_count: number;
  field_count: number;
  resource_count: number;
  depends_on: readonly string[];
  depended_by: readonly string[];
}

const MAX_CARD_KEYWORDS = 5;

/**
 * The Odoo-style app card body — name, description, keyword chips, and the
 * source/state provenance line. Composed inside the shared `BoardView` card frame
 * (via `ListView`'s `renderCard`), so it carries no interactive elements: the
 * frame owns the open-detail click and the footer owns the lifecycle actions.
 * The reflection enums take their tones from the addon's `statusTones` vocabulary.
 */
export function AddonCard({ row }: { row: AddonResourceRow }): ReactElement {
  const t = usePlatformT();
  // The JSON scalar may arrive null (a catalogue row whose keywords are unset) — guard the
  // boundary, and dedupe so the chip `key` stays unique.
  const keywords = [...new Set(row.keywords ?? [])].map((keyword) => ({ id: keyword, label: keyword }));
  return (
    <div className="grid min-w-0 gap-2">
      <span className="block min-w-0">
        <span className={textRoleVariants({ role: "title", truncate: true })}>{row.label}</span>
        <span className={textRoleVariants({ role: "caption", truncate: true })}>{row.id}</span>
      </span>
      {row.description ? (
        <p className={textRoleVariants({ role: "description" })}>{row.description}</p>
      ) : null}
      <OverflowChipList items={keywords} max={MAX_CARD_KEYWORDS} />
      <span className="flex flex-wrap items-center gap-1">
        <StatusBadge value={row.state}>{addonEnumLabel(t, "state", row.state)}</StatusBadge>
        <StatusBadge value={row.source}>{addonEnumLabel(t, "source", row.source)}</StatusBadge>
        {row.forced ? <Chip tone="info">{t("apps.required")}</Chip> : null}
        {row.pending ? <Chip tone="warning">{t("apps.pending")}</Chip> : null}
      </span>
    </div>
  );
}

/** Root changes use a revision-bound preview; packages use native held dependency jobs. */
export function AddonCardActions({
  row,
  context,
}: {
  row: AddonResourceRow;
  context: CardActionContext;
}): ReactNode {
  const t = usePlatformT();
  const settle = useActionResultRun({ noResultTitle: t("apps.actionFailed") });
  const [action, setAction] = useState<AddonChangeAction | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const applyingRef = useRef(false);
  const preview = useAuthoredQuery(
    AddonChangePreview,
    { addon: row.id, action: action ?? "ENABLE" },
    { enabled: action !== null },
  );
  // Invalidate the board only on an *effective* write — a server refusal (`ok: false`)
  // changed nothing, so it should not trigger a refetch.
  const [enable, enableState] = useAuthoredResourceMutation(EnableAddon, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
    shouldInvalidate: (data) => Boolean(data?.enable?.ok),
  });
  const [disable, disableState] = useAuthoredResourceMutation(DisableAddon, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
    shouldInvalidate: (data) => Boolean(data?.disable?.ok),
  });
  const [install, installState] = useAuthoredResourceMutation(InstallAddon, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
  });
  const [uninstall, uninstallState] = useAuthoredResourceMutation(UninstallAddon, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
  });
  const queueDependency = async (remove: boolean) => {
    if (applyingRef.current) return;
    applyingRef.current = true;
    try {
      await settle(async () => {
        const operation = remove
          ? (await uninstall({ addon: row.id }))?.uninstall
          : (await install({ addon: row.id }))?.install;
        return operation ? { ok: true, message: t("apps.dependencyQueued") } : null;
      });
    } finally {
      applyingRef.current = false;
    }
  };
  const applying = enableState.fetching || disableState.fetching || installState.fetching || uninstallState.fetching;
  const busy = applying || preview.isFetching;

  const run = useCallback(
    async () => {
      const change = preview.data?.addon_change_preview;
      if (!action || !change?.can_apply || applyingRef.current) return;
      applyingRef.current = true;
      try {
        const outcome = await settle(async () => action === "ENABLE"
          ? (await enable({ addon: row.id, revision: change.revision }))?.enable
          : (await disable({ addon: row.id, revision: change.revision }))?.disable);
        if (outcome?.ok) {
          setAction(null);
          context.refresh();
        } else {
          setApplyError(outcome?.message ?? t("apps.actionFailed"));
          await preview.refetch();
        }
      } finally {
        applyingRef.current = false;
      }
    },
    [action, context, disable, enable, preview.data, preview.refetch, row.id, settle, t],
  );

  const dialog = action ? (
    <AddonChangeDialog
      action={action}
      addonLabel={row.label}
      preview={preview.data?.addon_change_preview ?? null}
      loading={preview.isFetching}
      applying={applying}
      error={preview.error ? errorMessage(preview.error, t("apps.actionFailed")) : applyError}
      onRetry={() => { setApplyError(null); void preview.refetch(); }}
      onConfirm={() => { void run(); }}
      onCancel={() => { setAction(null); setApplyError(null); }}
    />
  ) : null;

  // Pending first: a queued enable or disable shows the restart state and
  // hides the live action, so a composed-but-disabled root cannot be queued twice.
  if (row.pending) return null;
  if (row.state === "ENABLED") {
    return (<>
      <Button
        size="sm"
        variant="ghost"
        disabled={busy}
        title={row.forced ? t("apps.forcedHint") : undefined}
        onClick={() => { setApplyError(null); setAction("DISABLE"); }}
      >
        <Glyph decorative name="minus" />
        {t("apps.disable")}
      </Button>
      {dialog}
    </>);
  }
  if (row.source === "REMOTE" || row.state === "REMOVED") {
    return (
      <Button size="sm" variant="primary" disabled={busy} onClick={() => { void queueDependency(false); }}>
        <Glyph decorative name="plus" />
        {t("apps.install")}
      </Button>
    );
  }
  return (<>
    <Button size="sm" variant="primary" disabled={busy} onClick={() => { setApplyError(null); setAction("ENABLE"); }}>
      <Glyph decorative name="plus" />
      {t("apps.enable")}
    </Button>
    <Button size="sm" variant="ghost" disabled={busy} onClick={() => { void queueDependency(true); }}>
      <Glyph decorative name="minus" />
      {t("apps.uninstall")}
    </Button>
    {dialog}
  </>);
}
