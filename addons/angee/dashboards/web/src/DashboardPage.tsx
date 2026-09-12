import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Button,
  DashboardSurface,
  HOME_PATH_PREFERENCE_KEY,
  Input,
  readRuntimeRouteShortcuts,
  ROUTE_SHORTCUTS_PREFERENCE_KEY,
  useDashboardRegistry,
  useRouteParam,
  useRouteHref,
  useRuntimeUserPreferences,
} from "@angee/ui";
import type { DashboardTarget } from "@angee/ui/dashboard/headless";

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
  const state = binding.state;
  const [pending, setPending] = React.useState(false);
  const [error, setError] = React.useState<Error | null>(null);
  const [sharing, setSharing] = React.useState(false);
  const [subjectId, setSubjectId] = React.useState("");
  const [subjectType, setSubjectType] = React.useState<"user" | "group">("user");
  const [shareRole, setShareRole] = React.useState<"viewer" | "editor">("viewer");
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

  const grantShare = async () => {
    if (state.status !== "ready" || !subjectId.trim()) return;
    setPending(true);
    setError(null);
    try {
      await binding.grantShare(state.persistedId, { subjectId: subjectId.trim(), subjectType, role: shareRole });
      setSubjectId("");
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
    <main className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-end gap-2 border-b border-border-subtle bg-sheet px-3 py-2">
        {error ? <span role="alert" className="mr-auto text-12 text-danger-text">{error.message}</span> : null}
        {target.scope === "personal" && state.status === "ready" && state.capabilities.canEdit ? (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setEditingDetails((value) => !value)}>Details</Button>
        ) : null}
        {href && preferences.available ? (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
            setPending(true);
            setError(null);
            void preferences.patchPreferences((current) => ({
              ...current,
              [HOME_PATH_PREFERENCE_KEY]: isHome ? null : href,
            })).catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
          }}>{isHome ? "Unset home" : "Set as home"}</Button>
        ) : null}
        {href && preferences.available && state.status === "ready" ? (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
            setPending(true);
            setError(null);
            void preferences.patchPreferences((current) => {
              const shortcuts = readRuntimeRouteShortcuts(current).filter((shortcut) => shortcut.id !== shortcutId);
              if (!pinned) shortcuts.push({ id: shortcutId, label: state.name, path: href, icon: "dashboards" });
              return { ...current, [ROUTE_SHORTCUTS_PREFERENCE_KEY]: shortcuts };
            }).catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause)))).finally(() => setPending(false));
          }}>{pinned ? "Unpin" : "Pin"}</Button>
        ) : null}
        {state.status === "ready" ? <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => void duplicate()}>Duplicate</Button> : null}
        {state.status === "ready" && state.capabilities.canShare ? (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setSharing((value) => !value)}>Share</Button>
        ) : null}
        {state.status === "ready" && target.scope === "personal" && state.capabilities.canArchive ? (
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => void archive()}>Archive</Button>
        ) : null}
      </div>
      {editingDetails && target.scope === "personal" && state.status === "ready" && state.capabilities.canEdit ? (
        <section className="flex flex-wrap items-center gap-2 border-b border-border-subtle bg-inset p-3" aria-label="Dashboard details">
          <Input size="sm" value={dashboardName} onChange={(event) => setDashboardName(event.target.value)} aria-label="Dashboard name" placeholder="Dashboard name" className="w-64" />
          <Input size="sm" value={dashboardDescription} onChange={(event) => setDashboardDescription(event.target.value)} aria-label="Dashboard description" placeholder="Description" className="min-w-72 flex-1" />
          <Button type="button" variant="primary" size="sm" disabled={pending || !dashboardName.trim()} onClick={() => void saveDetails()}>Save details</Button>
          <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => setEditingDetails(false)}>Cancel</Button>
        </section>
      ) : null}
      {sharing && state.status === "ready" && state.capabilities.canShare ? (
        <section className="flex flex-col gap-2 border-b border-border-subtle bg-inset p-3" aria-label="Dashboard sharing">
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label="Recipient type" value={subjectType} onChange={(event) => setSubjectType(event.target.value as "user" | "group")} className="h-8 rounded-6 border border-border bg-sheet px-2 text-12 text-fg">
              <option value="user">User</option><option value="group">Group</option>
            </select>
            <Input size="sm" value={subjectId} onChange={(event) => setSubjectId(event.target.value)} placeholder={`${subjectType} public ID`} aria-label="Recipient public ID" className="w-64" />
            <select aria-label="Dashboard role" value={shareRole} onChange={(event) => setShareRole(event.target.value as "viewer" | "editor")} className="h-8 rounded-6 border border-border bg-sheet px-2 text-12 text-fg">
              <option value="viewer">Viewer</option><option value="editor">Editor</option>
            </select>
            <Button type="button" variant="primary" size="sm" disabled={pending || !subjectId.trim()} onClick={() => void grantShare()}>Add recipient</Button>
          </div>
          {binding.sharesError ? <p role="alert" className="text-12 text-danger-text">{binding.sharesError.message}</p> : null}
          {binding.sharesLoading ? <p className="text-12 text-fg-muted">Loading recipients…</p> : null}
          {binding.shares.map((share) => (
            <div key={share.id} className="flex items-center gap-2 text-13 text-fg">
              <span className="min-w-0 flex-1 truncate">{share.label} · {share.subjectType} · {share.role}</span>
              <Button type="button" variant="ghost" size="sm" disabled={pending} onClick={() => {
                setPending(true);
                setError(null);
                void binding.revokeShare(state.persistedId, share)
                  .catch((cause) => setError(cause instanceof Error ? cause : new Error(String(cause))))
                  .finally(() => setPending(false));
              }}>Remove</Button>
            </div>
          ))}
        </section>
      ) : null}
      <DashboardSurface target={target} />
    </main>
  );
}
