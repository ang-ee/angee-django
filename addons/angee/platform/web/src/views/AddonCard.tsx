import { useCallback, type ReactElement, type ReactNode } from "react";
import {
  Badge,
  Button,
  Chip,
  Glyph,
  errorMessage,
  statusTone,
  textRoleVariants,
  useAuthoredMutation,
  useToast,
  type CardActionContext,
  type Tone,
} from "@angee/ui";

import { InstallAddon, UninstallAddon } from "../documents";
import { usePlatformT } from "../i18n";

/** The reflection resource the board reads + invalidates after every lifecycle write. */
export const ADDON_MODEL = "platform.Addon";

// The `platform.Addon` Hasura resource row (`hasura_model_resource` over the
// system-synced reflection table) plus the VCS marketplace tier's `vcs_path`. Raw
// snake fields, fetched + grouped client-side by the board's client row model.
export interface AddonResourceRow extends Record<string, unknown> {
  id: string;
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
  vcs_path: string;
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
  // The JSON scalar may arrive null (a rollup whose keywords are unset) — guard the
  // boundary, and dedupe so the chip `key` stays unique.
  const keywords = [...new Set(row.keywords ?? [])].slice(0, MAX_CARD_KEYWORDS);
  return (
    <div className="grid min-w-0 gap-2">
      <span className="block min-w-0">
        <span className="block truncate text-sm font-semibold text-fg">{row.label}</span>
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
        <Badge tone={statusTone(row.state, STATE_TONES)}>{t(`platform.state.${row.state}`)}</Badge>
        <Badge tone={statusTone(row.source, SOURCE_TONES, { unknownTone: "neutral" })}>
          {t(`platform.source.${row.source}`)}
        </Badge>
        {row.forced ? <Badge tone="info">{t("platform.apps.required")}</Badge> : null}
        {row.pending ? <Badge tone="warning">{t("platform.apps.pending")}</Badge> : null}
      </span>
    </div>
  );
}

/**
 * The card footer lifecycle controls. An enabled addon offers Uninstall (locked for a
 * forced/depended-on addon — the server refuses it too); an available/removed one
 * offers Install, or shows the pending-restart state once it is queued. Both writes go
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
  // Invalidate the board only on an *effective* write — a server refusal (`ok: false`)
  // changed nothing, so it should not trigger a refetch.
  const [install, installState] = useAuthoredMutation(InstallAddon, {
    invalidateModels: [ADDON_MODEL],
    shouldInvalidate: (data) => Boolean(data?.install?.ok),
  });
  const [uninstall, uninstallState] = useAuthoredMutation(UninstallAddon, {
    invalidateModels: [ADDON_MODEL],
    shouldInvalidate: (data) => Boolean(data?.uninstall?.ok),
  });
  const busy = installState.fetching || uninstallState.fetching;

  const run = useCallback(
    async (kind: "install" | "uninstall") => {
      try {
        const result =
          kind === "install"
            ? (await install({ addon: row.id }))?.install
            : (await uninstall({ addon: row.id }))?.uninstall;
        if (result?.ok) {
          toast.success({ title: result.message });
          context.refresh();
        } else {
          toast.danger({ title: result?.message ?? t("platform.apps.actionFailed") });
        }
      } catch (cause) {
        toast.danger({ title: errorMessage(cause, t("platform.apps.actionFailed")) });
      }
    },
    [install, uninstall, row.id, toast, t, context],
  );

  // Pending first: a queued change (install *or* uninstall) shows the restart state and
  // hides the live action, so a composed-but-removed root cannot be uninstalled twice.
  if (row.pending) {
    return (
      <Button size="sm" variant="ghost" disabled>
        {t("platform.apps.pendingRestart")}
      </Button>
    );
  }
  if (row.state === "enabled") {
    return (
      <Button
        size="sm"
        variant="ghost"
        disabled={row.forced || busy}
        title={row.forced ? t("platform.apps.forcedHint") : undefined}
        onClick={() => void run("uninstall")}
      >
        <Glyph decorative name="trash" />
        {t("platform.apps.uninstall")}
      </Button>
    );
  }
  if (row.source === "remote") {
    // Known from a marketplace source but not materialised — the local installer
    // cannot clone it, so installing would write an unbootable settings.yaml.
    // Materialising is an operator-tier step; until then the action is locked.
    return (
      <Button size="sm" variant="ghost" disabled title={t("platform.apps.remoteHint")}>
        <Glyph decorative name="plus" />
        {t("platform.apps.install")}
      </Button>
    );
  }
  return (
    <Button size="sm" variant="primary" disabled={busy} onClick={() => void run("install")}>
      <Glyph decorative name="plus" />
      {t("platform.apps.install")}
    </Button>
  );
}
