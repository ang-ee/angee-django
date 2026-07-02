import { createNamespaceT, type BaseMenuItem } from "@angee/ui";
import type { I18nResources } from "@angee/refine";

export function enOperatorBundleForMenu(
  menu: BaseMenuItem,
): I18nResources {
  const titles: Record<string, string> = {};
  for (const section of menu.children ?? []) {
    const sectionId = section.id ?? section.route;
    if (!sectionId || !section.label) continue;
    titles[`section.${sectionId}.title`] = section.label;
  }
  return { operator: titles } satisfies I18nResources;
}

// The logs drawer's rail-tab label + overlay aria-label. The drawer manifest is
// static (no hook context to call `useOperatorT`), so the title is a constant the
// `operator` namespace also publishes (below) — one source of the copy.
export const operatorLogsDrawerTitle = "Logs";

/**
 * The operator console's copy beyond the section titles (those come from
 * `enOperatorBundleForMenu`): table headers, row/stack action labels, empty and
 * loading messages, confirm dialogs, card titles, and the set-secret form. Keyed
 * under the `operator` namespace and merged into the manifest bundle in `index.ts`.
 */
export const enOperatorMessages: Record<string, string> = {
  // Shared table chrome.
  "table.actions": "Actions",

  // Shared live-log panel.
  "logs.live": "Live",
  "logs.connecting": "Connecting",
  "logs.error": "Disconnected",
  "logs.empty": "Waiting for log output…",

  // Logs drawer (console-shell drawer adopter).
  "logs.drawerTitle": operatorLogsDrawerTitle,
  "logs.target.label": "Log source",
  "logs.target.placeholder": "Select a service or workspace…",
  "logs.target.empty.title": "No log source selected",
  "logs.target.empty.description":
    "Pick a service or workspace to stream its logs.",

  // Transport / connection states.
  "transport.connecting": "Connecting to operator",
  "transport.unavailable.title": "Operator daemon unavailable",
  "transport.unavailable.description":
    "Operator daemon is not configured for this user.",
  "transport.noConsoleClient": 'No "console" GraphQL client is configured.',
  "transport.unknownError": "Unknown operator error.",

  // Overview.
  "overview.loading": "Loading overview",
  "overview.stack.title": "Stack",
  "overview.stack.empty": "No stack status.",
  "overview.health.title": "Health",
  "overview.health.empty": "No health report.",

  // Services.
  "services.loading": "Loading services",
  "services.empty": "No services.",
  "services.column.name": "Name",
  "services.column.runtime": "Runtime",
  "services.column.status": "Status",
  "services.column.health": "Health",
  "services.start": "Start",
  "services.restart": "Restart",
  "services.recreate": "Recreate",
  "services.stop": "Stop",
  "services.destroy": "Destroy",
  "services.destroy.confirm.title": "Destroy service?",
  "services.destroy.confirm.body":
    "“{name}” will be stopped and removed from the stack — the workspace it mounts is left intact.",
  "services.detail.notFound": "Service not found",
  "services.detail.overview": "Overview",
  "services.detail.endpoint": "Endpoint",
  "services.detail.internal": "Internal address",
  "services.detail.logs": "Logs",

  // Workspaces.
  "workspaces.loading": "Loading workspaces",
  "workspaces.empty": "No workspaces.",
  "workspaces.column.name": "Name",
  "workspaces.column.template": "Template",
  "workspaces.column.path": "Path",
  "workspaces.column.port": "Port",
  "workspaces.column.ttl": "TTL",
  "workspaces.syncBase": "Sync base",
  "workspaces.destroy": "Destroy",
  "workspaces.destroy.confirm.title": "Destroy workspace?",
  "workspaces.destroy.confirm.body":
    "“{name}” will be destroyed — its files are removed and this cannot be undone.",
  "workspaces.detail.notFound": "Workspace not found",
  "workspaces.detail.overview": "Overview",
  "workspaces.detail.expiresAt": "Expires",
  "workspaces.detail.mcp": "Playwright MCP",
  "workspaces.detail.logs": "Logs",
  "workspaceSources.title": "Sources",
  "workspaceSources.empty": "No sources.",
  "workspaceSources.column.slot": "Slot",
  "workspaceSources.column.state": "State",
  "workspaceSources.column.branch": "Branch",
  "workspaceSources.column.drift": "Drift",
  "workspaceSources.column.path": "Path",
  "workspaceSources.dirty": "dirty",
  "workspaceSources.clean": "clean",

  // Sources.
  "sources.loading": "Loading sources",
  "sources.empty": "No sources.",
  "sources.column.name": "Name",
  "sources.column.kind": "Kind",
  "sources.column.status": "Status",
  "sources.column.branch": "Branch",
  "sources.column.aheadBehind": "Ahead/Behind",
  "sources.column.dirty": "Dirty",
  "sources.dirty": "dirty",
  "sources.clean": "clean",
  "sources.fetch": "Fetch",
  "sources.pull": "Pull",
  "sources.push": "Push",
  "sources.detail.notFound": "Source not found",
  "sources.detail.overview": "Overview",
  "sources.detail.path": "Path",
  "sources.detail.upstream": "Upstream",
  "sources.detail.currentRef": "Current ref",
  "sources.detail.pushed": "Pushed",
  "sources.detail.error": "Error",

  // GitOps.
  "gitops.loading": "Loading GitOps topology",
  "gitops.empty.title": "No GitOps topology",
  "gitops.links.empty": "No GitOps links.",
  "gitops.summary.clean": "Clean",
  "gitops.summary.dirty": "Dirty",
  "gitops.summary.ahead": "Ahead",
  "gitops.summary.behind": "Behind",
  "gitops.summary.diverged": "Diverged",
  "gitops.summary.unpushed": "Unpushed",
  "gitops.column.source": "Source",
  "gitops.column.workspace": "Workspace",
  "gitops.column.slot": "Slot",
  "gitops.column.status": "Status",
  "gitops.column.branch": "Branch",
  "gitops.column.aheadBehind": "Ahead/Behind",
  "gitops.column.pushed": "Pushed",
  "gitops.pushed.yes": "yes",
  "gitops.pushed.no": "no",

  // Operations.
  "operations.loading": "Loading operations",
  "operations.empty": "No jobs.",
  "operations.column.name": "Name",
  "operations.column.runtime": "Runtime",
  "operations.run": "Run",
  "operations.stack.title": "Stack lifecycle",
  "operations.stack.build": "Build",
  "operations.stack.up": "Up",
  "operations.stack.down": "Down",
  "operations.stack.destroy": "Destroy",
  "operations.stack.destroy.confirm.title": "Destroy stack?",
  "operations.stack.destroy.confirm.body":
    "All services and runtime state are removed. This cannot be undone.",

  // Templates.
  "templates.loading": "Loading templates",
  "templates.empty.title": "No templates",
  "templates.column.name": "Template",
  "templates.column.kind": "Kind",
  "templates.column.path": "Path",
  "templates.inputs": "Inputs",
  "templates.input.required": "required",
  "templates.input.optional": "optional",
  "templates.input.immutable": "immutable",
  "templates.input.generated": "generated",

  // Secrets.
  "secrets.loading": "Loading secrets",
  "secrets.empty": "No declared secrets.",
  "secrets.column.name": "Name",
  "secrets.column.declared": "Declared",
  "secrets.column.hasValue": "Has value",
  "secrets.column.required": "Required",
  "secrets.column.envVar": "Env var",
  "secrets.column.actions": "Actions",
  "secrets.yes": "yes",
  "secrets.no": "no",
  "secrets.value.set": "set",
  "secrets.value.empty": "empty",
  "secrets.protected": "Protected",
  "secrets.protected.hint":
    "Control-plane secret (required or generated) — cannot be deleted from the console.",
  "secrets.delete": "Delete",
  "secrets.delete.confirm.title": "Delete secret?",
  "secrets.delete.confirm.body":
    "“{name}” will be removed from the secrets backend.",
  "secrets.form.title": "Set a secret",
  "secrets.form.name": "Name",
  "secrets.form.namePlaceholder": "SECRET_NAME",
  "secrets.form.value": "Value",
  "secrets.form.valuePlaceholder": "value",
  "secrets.form.submit": "Set",
  "secrets.set.label": "Set secret",
  "secrets.delete.label": "Delete secret",
};

// A translator bound to the `operator` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useOperatorT = createNamespaceT("operator", enOperatorMessages);
