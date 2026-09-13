import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Button,
  DashboardSurface,
  DropdownMenu,
  ErrorBanner,
  Glyph,
  HOME_PATH_PREFERENCE_KEY,
  Input,
  Page,
  PageToolbar,
  readRuntimeRouteShortcuts,
  ROUTE_SHORTCUTS_PREFERENCE_KEY,
  RecordChrome,
  useDashboardRegistry,
  useRouteParam,
  useRouteHref,
  useRuntimeUserPreferences,
} from "@angee/ui";
import type { DashboardTarget } from "@angee/ui/dashboard/headless";
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
  const store = useDashboardRegistry().store;
  if (!store) return <DashboardSurface target={target} />;
  return <StoredDashboardPage target={target} store={store} />;
}

function StoredDashboardPage({ target, store }: {
  target: DashboardTarget;
  store: NonNullable<ReturnType<typeof useDashboardRegistry>["store"]>;
}): React.ReactElement {
  const binding = store.useDashboard(target);
  const preferences = useRuntimeUserPreferences();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const t = useDashboardsT();
  const state = binding.state;
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<Error | null>(null);
  const [editingDetails, setEditingDetails] = React.useState(false);
  const [dashboardName, setDashboardName] = React.useState("");
  const [dashboardDescription, setDashboardDescription] = React.useState("");
  const href = target.scope === "personal" ? routeHref("dashboards.detail", { id: target.id }) : null;
  const shortcutId = state.status === "ready" ? `dashboard:${state.persistedId}` : "";
  const pinned = shortcutId
    ? readRuntimeRouteShortcuts(preferences.preferences).some((shortcut) => shortcut.id === shortcutId)
    : false;
  const isHome = href !== null && preferences.preferences[HOME_PATH_PREFERENCE_KEY] === href;
  React.useEffect(() => {
    if (state.status !== "ready" || editingDetails) return;
    setDashboardName(state.name);
    setDashboardDescription(state.description ?? "");
  }, [editingDetails, state]);

  const duplicate = async () => {
    if (state.status !== "ready") return;
    setPending(true);
    setError(null);
    try {
      const result = await binding.duplicate(target, {
        name: `${state.name} copy`,
        clientCreationKey: globalThis.crypto?.randomUUID?.() ?? `dashboard-copy-${Date.now()}`,
      });
      await navigate({ to: routeHref("dashboards.detail", { id: result.persistedId }) });
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setPending(false);
    }
  };

  const saveDetails = async () => {
    if (state.status !== "ready" || !dashboardName.trim()) return;
    setPending(true);
    setError(null);
    try {
      await binding.save({
        target,
        persistedId: state.persistedId,
        expectedRevision: state.revision,
        name: dashboardName.trim(),
        description: dashboardDescription,
        snapshot: state.snapshot,
      });
      setEditingDetails(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setPending(false);
    }
  };

  const archive = async () => {
    if (state.status !== "ready" || target.scope !== "personal") return;
    setPending(true);
    setError(null);
    try {
      await binding.archive(target.id, state.revision, true);
      await preferences.patchPreferences((current) => ({
        ...current,
        [ROUTE_SHORTCUTS_PREFERENCE_KEY]: readRuntimeRouteShortcuts(current).filter((shortcut) => shortcut.id !== shortcutId),
        ...(current[HOME_PATH_PREFERENCE_KEY] === href ? { [HOME_PATH_PREFERENCE_KEY]: null } : {}),
      }));
      await navigate({ to: routeHref("dashboards.index") });
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setPending(false);
    }
  };

  return (
    <Page>
      <DashboardSurface
        target={target}
        toolbar={(
          <>
            <PageToolbar
              className="flex-wrap"
              start={target.scope === "personal" && state.status === "ready" && state.capabilities.canEdit ? (
                <Button type="button" variant="secondary" size="sm" disabled={pending} onClick={() => setEditingDetails((value) => !value)}>
                  <Glyph name="pencil" />{t("common.details")}
                </Button>
              ) : null}
              end={state.status === "ready" ? (
                <>
                  <RecordChrome value={{
                    resource: "dashboards.Dashboard",
                    canonicalResource: "dashboards.Dashboard",
                    dataProviderName: "console",
                    recordId: state.persistedId,
                    record: { id: state.persistedId, displayName: state.name, name: state.name },
                  }} />
                  <DropdownMenu.Root>
                    <DropdownMenu.Trigger
                      render={(
                        <Button type="button" variant="ghost" size="sm" disabled={pending}>
                          <Glyph name="more-horizontal" fallbackName="more-vertical" />
                          {t("common.moreActions")}
                        </Button>
                      )}
                    />
                    <DropdownMenu.Portal>
                      <DropdownMenu.Positioner sideOffset={6} align="end">
                        <DropdownMenu.Content className="w-52">
                  {href && preferences.available ? (
                    <>
                      <DropdownMenu.Item onClick={() => {
                        setPending(true);
                        setError(null);
                        void preferences.patchPreferences((current) => {
                          const shortcuts = readRuntimeRouteShortcuts(current).filter((shortcut) => shortcut.id !== shortcutId);
                          if (!pinned) shortcuts.push({ id: shortcutId, label: state.name, path: href, icon: "dashboard" });
                          return { ...current, [ROUTE_SHORTCUTS_PREFERENCE_KEY]: shortcuts };
                        }).catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
                      }}>
                        <Glyph name="star" />{pinned ? t("common.unpin") : t("common.pin")}
                      </DropdownMenu.Item>
                      <DropdownMenu.Item onClick={() => {
                        setPending(true);
                        setError(null);
                        void preferences.patchPreferences((current) => ({
                          ...current,
                          [HOME_PATH_PREFERENCE_KEY]: isHome ? null : href,
                        })).catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
                      }}>
                        <Glyph name="home" />{isHome ? t("common.unsetHome") : t("common.setAsHome")}
                      </DropdownMenu.Item>
                    </>
                  ) : null}
                  <DropdownMenu.Item onClick={() => void duplicate()}>
                    <Glyph name="copy" />{t("common.duplicate")}
                  </DropdownMenu.Item>
                  {target.scope === "personal" && state.capabilities.canArchive ? (
                    <>
                      <DropdownMenu.Separator />
                      <DropdownMenu.Item variant="danger" onClick={() => void archive()}>
                        <Glyph name="archive" />{t("common.archive")}
                      </DropdownMenu.Item>
                    </>
                  ) : null}
                        </DropdownMenu.Content>
                      </DropdownMenu.Positioner>
                    </DropdownMenu.Portal>
                  </DropdownMenu.Root>
                </>
              ) : null}
            />
            <ErrorBanner description={error?.message ?? null} />
            {editingDetails && target.scope === "personal" && state.status === "ready" && state.capabilities.canEdit ? (
        <section
          className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-inset p-3"
          aria-label={t("details.label")}
          onKeyDown={(event) => {
            if (event.key === "Escape") setEditingDetails(false);
          }}
        >
          <Input size="sm" value={dashboardName} onChange={(event) => setDashboardName(event.target.value)} aria-label={t("details.name")} placeholder={t("details.name")} className="min-w-40 flex-1 sm:w-64 sm:flex-none" />
          <Input size="sm" value={dashboardDescription} onChange={(event) => setDashboardDescription(event.target.value)} aria-label={t("details.description")} placeholder={t("details.description")} className="min-w-40 flex-1" />
          <Button type="button" variant="primary" size="sm" disabled={pending || !dashboardName.trim()} onClick={() => void saveDetails()}>{t("details.save")}</Button>
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setEditingDetails(false)}>{t("common.cancel")}</Button>
        </section>
            ) : null}

          </>
        )}
      />
    </Page>
  );
}
