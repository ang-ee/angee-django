import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  ActionMenu,
  ActionTrigger,
  Button,
  DashboardSurface,
  errorMessage,
  DropdownMenu,
  Glyph,
  HOME_PATH_PREFERENCE_KEY,
  MutationDialog,
  Page,
  PageToolbar,
  mutationDialogValueCodecs,
  readRuntimeRouteShortcuts,
  ROUTE_SHORTCUTS_PREFERENCE_KEY,
  RecordChrome,
  useActionResultRun,
  useBreadcrumbLeafLabel,
  useDashboardRegistry,
  useRouteParam,
  useRouteHref,
  useRuntimeUserPreferences,
  useTrashRecord,
  useToast,
  type DescriptorField,
  type MutationDialogValues,
} from "@angee/ui";
import { useModelMetadata } from "@angee/metadata";
import type { DashboardTarget } from "@angee/ui/dashboard/headless";
import { visibleDashboardSnapshot } from "@angee/ui/dashboard/surface";
import { useDashboardsT } from "./i18n";

export function PersonalDashboardPage(): React.ReactElement {
  const id = useRouteParam("id") ?? "";
  return <DashboardPage target={{ scope: "personal", id }} />;
}

export function ResourceDashboardPage(): React.ReactElement {
  const key = useRouteParam("key") ?? "";
  return <DashboardPage target={{ scope: "resource", key }} />;
}

export function AddonDashboardPage(): React.ReactElement {
  const key = useRouteParam("key") ?? "";
  return <DashboardPage target={{ scope: "addon", key }} />;
}

function DashboardPage({ target }: { target: DashboardTarget }): React.ReactElement {
  const registry = useDashboardRegistry();
  const store = registry.store;
  if (!store) return <DashboardSurface target={target} />;
  return <StoredDashboardPage target={target} registry={registry} store={store} />;
}

function StoredDashboardPage({ target, registry, store }: {
  target: DashboardTarget;
  registry: ReturnType<typeof useDashboardRegistry>;
  store: NonNullable<ReturnType<typeof useDashboardRegistry>["store"]>;
}): React.ReactElement {
  const binding = store.useDashboard(target);
  const preferences = useRuntimeUserPreferences();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const t = useDashboardsT();
  const toast = useToast();
  const trashRecord = useTrashRecord(useModelMetadata("dashboards.Dashboard")?.resource);
  // Commands settle through the shared run; trash already owns its outcome.
  const settle = useActionResultRun({ noResultTitle: t("common.actionFailed") });
  const state = binding.state;
  useBreadcrumbLeafLabel(state.status === "ready" ? state.name : null);
  const [pending, setPending] = React.useState(false);
  const href = target.scope === "personal" ? routeHref("dashboards.detail", { id: target.id }) : null;
  const shortcutId = state.status === "ready" ? `dashboard:${state.persistedId}` : "";
  const pinned = shortcutId
    ? readRuntimeRouteShortcuts(preferences.preferences).some((shortcut) => shortcut.id === shortcutId)
    : false;
  const isHome = href !== null && preferences.preferences[HOME_PATH_PREFERENCE_KEY] === href;

  const run = async (work: () => Promise<void>, message: string): Promise<void> => {
    setPending(true);
    try {
      await settle(async () => { await work(); return { ok: true, message }; });
    } finally {
      setPending(false);
    }
  };

  const duplicate = () => {
    if (state.status !== "ready") return;
    void run(async () => {
      const result = await binding.duplicate(target, {
        name: t("common.copyName", { name: state.name }),
        idempotencyKey: globalThis.crypto?.randomUUID?.() ?? `dashboard-copy-${Date.now()}`,
        snapshot: visibleDashboardSnapshot(registry, target, state),
      });
      await navigate({ to: routeHref("dashboards.detail", { id: result.persistedId }) });
    }, t("common.duplicated"));
  };

  const trash = () => {
    if (state.status !== "ready" || target.scope !== "personal") return;
    setPending(true);
    void (async () => {
      try {
        // The shared verb confirms, moves the dashboard to the trash and settles the outcome.
        if (!(await trashRecord.trash(target.id, state.name))) return;
        try {
          await preferences.patchPreferences((current) => ({
            ...current,
            [ROUTE_SHORTCUTS_PREFERENCE_KEY]: readRuntimeRouteShortcuts(current).filter((shortcut) => shortcut.id !== shortcutId),
            ...(current[HOME_PATH_PREFERENCE_KEY] === href ? { [HOME_PATH_PREFERENCE_KEY]: null } : {}),
          }));
        } finally {
          // The dashboard is gone even if its preference cleanup failed.
          await navigate({ to: routeHref("dashboards.index") });
        }
      } catch (error) {
        toast.danger({ title: errorMessage(error, t("common.actionFailed")) });
      } finally {
        setPending(false);
      }
    })();
  };

  const detailFields = React.useMemo<readonly DescriptorField[]>(() => [
    { name: "name", label: t("details.name"), required: true },
    { name: "description", label: t("details.description") },
  ], [t]);

  return (
    <Page>
      <DashboardSurface
        target={target}
        toolbar={(
          <PageToolbar
            className="flex-wrap"
            start={target.scope === "personal" && state.status === "ready" && state.capabilities.canEdit ? (
              // The dashboard's own facts edit in the shared mutation dialog.
              <MutationDialog
                trigger={(
                  <Button type="button" variant="secondary" size="sm" disabled={pending}>
                    <Glyph name="pencil" />{t("common.details")}
                  </Button>
                )}
                title={t("details.label")}
                fields={detailFields}
                initialValues={{ name: state.name, description: state.description ?? "" }}
                submitLabel={t("details.save")}
                submittingLabel={t("details.saving")}
                errorFallback={t("common.actionFailed")}
                parseValues={(values: MutationDialogValues) => ({
                  name: mutationDialogValueCodecs.requiredString(values.name, "name"),
                  description: mutationDialogValueCodecs.string(values.description) ?? "",
                })}
                onSubmit={async ({ name, description }) => {
                  await binding.save({
                    target,
                    persistedId: state.persistedId,
                    expectedRevision: state.revision,
                    declarationRevision: state.declarationRevision,
                    name,
                    description,
                    snapshot: state.snapshot,
                  });
                  return { status: "ok", data: undefined, message: t("details.saved") };
                }}
              />
            ) : null}
            end={state.status === "ready" ? (
              <>
                <RecordChrome value={{
                  resource: "dashboards.Dashboard",
                  canonicalResource: "dashboards.Dashboard",
                  dataProviderName: "console",
                  recordId: state.persistedId,
                  record: { id: state.persistedId, displayName: state.name, name: state.name },
                  formReadOnly: !state.capabilities.canEdit,
                }} />
                <ActionMenu label={t("common.moreActions")} glyph="more-horizontal"
                  size="sm" align="end" blocked={pending} loading={pending}>
                  {href && preferences.available ? (
                    <>
                      <ActionTrigger glyph="star" onClick={() => void run(async () => {
                        await preferences.patchPreferences((current) => {
                          const shortcuts = readRuntimeRouteShortcuts(current).filter((shortcut) => shortcut.id !== shortcutId);
                          if (!pinned) shortcuts.push({ id: shortcutId, label: state.name, path: href, icon: "dashboard" });
                          return { ...current, [ROUTE_SHORTCUTS_PREFERENCE_KEY]: shortcuts };
                        });
                      }, t(pinned ? "common.unpinned" : "common.pinned"))}>
                        {pinned ? t("common.unpin") : t("common.pin")}
                      </ActionTrigger>
                      <ActionTrigger glyph="home" onClick={() => void run(async () => {
                        await preferences.patchPreferences((current) => ({
                          ...current,
                          [HOME_PATH_PREFERENCE_KEY]: isHome ? null : href,
                        }));
                      }, t(isHome ? "common.homeUnset" : "common.homeSet"))}>
                        {isHome ? t("common.unsetHome") : t("common.setAsHome")}
                      </ActionTrigger>
                    </>
                  ) : null}
                  <ActionTrigger glyph="copy" onClick={duplicate}>
                    {t("common.duplicate")}
                  </ActionTrigger>
                  {target.scope === "personal" && state.capabilities.canTrash ? (
                    <>
                      <DropdownMenu.Separator />
                      <ActionTrigger variant="danger" glyph="trash" onClick={trash}>
                        {t("common.trash")}
                      </ActionTrigger>
                    </>
                  ) : null}
                </ActionMenu>
              </>
            ) : null}
          />
        )}
      />
    </Page>
  );
}
