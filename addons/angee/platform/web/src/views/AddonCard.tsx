import { useCallback, useRef, useState, type ReactElement, type ReactNode } from "react";
import {
  Badge, Button, Chip, Glyph, errorMessage, statusTone, textRoleVariants, useAuthoredResourceMutation, useToast, type CardActionContext, type Tone } from "@angee/ui";
import { useAuthoredQuery } from "@angee/refine";

import {
  AddonChangePreview,
  DisableAddon,
  InstallAddon,
  PLATFORM_ADDON_MUTATION_INVALIDATES,
} from "../documents";
import { usePlatformT } from "../i18n";
import { addonDisplayLabel } from "../lib/rows";
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
  kind: string;
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

// The reflection enums color the same way wherever they render (card body + list
// columns), so the override maps live here once and both surfaces import them: the
// shared `statusTone` vocabulary owns the mechanism (`disabled` is already neutral
// there), with these platform-specific values supplied as the override.
export const STATE_TONES: Record<string, Tone> = {
  enabled: "success",
  removed: "danger",
};
export const SOURCE_TONES: Record<string, Tone> = {
  remote: "info",
};

/**
 * The Odoo-style app card body — name, description, keyword chips, and the
 * source/state provenance line. Composed inside the shared `BoardView` card frame
 * (via `ListView`'s `renderCard`), so it carries no interactive elements: the
 * frame owns the open-detail click and the footer owns the lifecycle actions.
 */
export function AddonCard({ row }: { row: AddonResourceRow }): ReactElement {
  const t = usePlatformT();
  // The JSON scalar may arrive null (a catalogue row whose keywords are unset) — guard the
  // boundary, and dedupe so the chip `key` stays unique.
  const keywords = [...new Set(row.keywords ?? [])].slice(0, MAX_CARD_KEYWORDS);
  return (
    <div className="grid min-w-0 gap-2">
      <span className="block min-w-0">
        <span className="block truncate text-sm font-semibold text-fg">{addonDisplayLabel(row.label, row.id)}</span>
        <span className={textRoleVariants({ role: "caption", truncate: true })}>{row.id}</span>
      </span>
      {row.description ? (
        <p className="line-clamp-2 text-13 text-fg-muted">{row.description}</p>
      ) : null}
      {keywords.length > 0 ? (
        <span className="flex flex-wrap gap-1">
          {keywords.map((keyword) => (
            <Chip key={keyword} tone="muted" size="sm">
              {keyword}
            </Chip>
          ))}
        </span>
      ) : null}
      <span className="flex flex-wrap items-center gap-1">
        <Badge tone={statusTone(row.state, STATE_TONES)}>{t(`state.${row.state}`)}</Badge>
        <Badge tone={statusTone(row.source, SOURCE_TONES, { unknownTone: "neutral" })}>
          {t(`source.${row.source}`)}
        </Badge>
        {row.forced ? <Badge tone="info">{t("apps.required")}</Badge> : null}
        {row.pending ? <Badge tone="warning">{t("apps.pending")}</Badge> : null}
      </span>
    </div>
  );
}

/**
 * The card footer lifecycle controls. An enabled addon offers Disable; required
 * addons still open the server-owned refusal preview. An available addon offers
 * Install; a removed addon offers Reinstall through the same server-owned preview.
 * Pending rows show the restart state once queued. Both writes go
 * through the platform AddonInstaller mutations and refetch the reflected board.
 */
export function AddonCardActions({
  row,
  context,
}: {
  row: AddonResourceRow;
  context: CardActionContext;
}): ReactNode {
  const t = usePlatformT();
  const toast = useToast();
  const [action, setAction] = useState<AddonChangeAction | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const applyingRef = useRef(false);
  const preview = useAuthoredQuery(
    AddonChangePreview,
    { addon: row.id, action: action ?? "INSTALL" },
    { enabled: action !== null },
  );
  // Invalidate the board only on an *effective* write — a server refusal (`ok: false`)
  // changed nothing, so it should not trigger a refetch.
  const [install, installState] = useAuthoredResourceMutation(InstallAddon, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
    shouldInvalidate: (data) => Boolean(data?.install?.ok),
  });
  const [disable, disableState] = useAuthoredResourceMutation(DisableAddon, {
    invalidateModels: PLATFORM_ADDON_MUTATION_INVALIDATES,
    shouldInvalidate: (data) => Boolean(data?.disable?.ok),
  });
  const applying = installState.fetching || disableState.fetching;
  const busy = applying || preview.isFetching;

  const run = useCallback(
    async () => {
      const change = preview.data?.addon_change_preview;
      if (!action || !change?.can_apply || applyingRef.current) return;
      applyingRef.current = true;
      try {
        const result =
          action === "INSTALL"
            ? (await install({ addon: row.id, revision: change.revision }))?.install
            : (await disable({ addon: row.id, revision: change.revision }))?.disable;
        if (result?.ok) {
          toast.success({ title: result.message });
          setAction(null);
          context.refresh();
        } else {
          const message = result?.message ?? t("apps.actionFailed");
          setApplyError(message);
          toast.danger({ title: message });
          await preview.refetch();
        }
      } catch (cause) {
        const message = errorMessage(cause, t("apps.actionFailed"));
        setApplyError(message);
        toast.danger({ title: message });
      } finally {
        applyingRef.current = false;
      }
    },
    [action, context, disable, install, preview.data, preview.refetch, row.id, t, toast],
  );

  const dialog = action ? (
    <AddonChangeDialog
      action={action}
      addonLabel={addonDisplayLabel(row.label, row.id)}
      preview={preview.data?.addon_change_preview ?? null}
      loading={preview.isFetching}
      applying={applying}
      error={preview.error ? errorMessage(preview.error, t("apps.actionFailed")) : applyError}
      onRetry={() => { setApplyError(null); void preview.refetch(); }}
      onConfirm={() => { void run(); }}
      onCancel={() => { setAction(null); setApplyError(null); }}
    />
  ) : null;

  // Pending first: a queued install or disable shows the restart state and
  // hides the live action, so a composed-but-disabled root cannot be queued twice.
  if (row.pending) return null;
  if (row.state === "enabled") {
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
  if (row.source === "remote") {
    // Known from a marketplace source but not materialised — the local installer
    // cannot clone it, so installing would write an unbootable settings.yaml.
    // Materialising is an operator-tier step; until then the action is locked.
    return (
      <Button size="sm" variant="ghost" disabled title={t("apps.remoteHint")}>
        <Glyph decorative name="plus" />
        {t("apps.install")}
      </Button>
    );
  }
  return (<>
    <Button size="sm" variant="primary" disabled={busy} onClick={() => { setApplyError(null); setAction("INSTALL"); }}>
      <Glyph decorative name="plus" />
      {t(row.state === "removed" ? "apps.reinstall" : "apps.install")}
    </Button>
    {dialog}
  </>);
}
