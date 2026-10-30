import type { MenuDeclarations } from "@angee/app";
import { createNamespaceT } from "@angee/ui";
import type { I18nResources } from "@angee/refine";

export function enOperatorBundleForMenu(
  menu: MenuDeclarations,
  parent: string,
): I18nResources {
  const titles: Record<string, string> = {};
  for (const [sectionId, section] of Object.entries(menu)) {
    if (section.parent !== parent || !section.label) continue;
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

  // Shared live-log panel.
  "logs.live": "Live",
  "logs.connecting": "Connecting",
  "logs.error": "Disconnected",
  "logs.empty": "Waiting for log output…",
  "logs.unavailable": "The operator daemon is unavailable.",
  "logs.closed": "The log stream closed (code {code}).",

  // Logs drawer (console-shell drawer adopter).
  "logs.target.label": "Log source",
  "logs.target.placeholder": "Select a service…",
  "logs.target.empty.title": "No log source selected",
  "logs.target.empty.description": "Pick a service to stream its logs.",

  // Transport / connection states.
  "transport.unknownError": "Unknown operator error.",

  // Overview.
  "overview.loading": "Loading overview…",
  "overview.error": "The overview could not be loaded",
  "overview.stack.title": "Stack",
  "overview.stack.name": "Name",
  "overview.stack.root": "Root",
  "overview.stack.empty": "No stack status.",
  "overview.health.title": "Health",
  "overview.health.status": "Status",
  "overview.health.service": "Service",
  "overview.health.empty": "No health report.",
  "overview.health.readinessUnavailable": "{service}: readiness unavailable",
  "overview.health.serviceState": "{service}: {state}",
  "overview.restart": "Restart application",
  "overview.restartUnavailable": "This stack has no application restart job configured.",

  // Services.
  "services.record": "Service",
  "services.loading": "Loading services…",
  "services.empty": "No services.",
  "services.column.name": "Name",
  "services.column.runtime": "Runtime",
  "services.column.status": "Status",
  "services.column.health": "Health",
  "services.status.exit": "exit {code}",
  "services.status.restarts": "restarts: {count}",
  "services.start": "Start",
  "services.restart": "Restart",
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
  "workspaces.record": "Workspace",
  "workspaces.loading": "Loading workspaces…",
  "workspaces.empty": "No workspaces.",
  "workspaces.column.name": "Name",
  "workspaces.column.template": "Template",
  "workspaces.column.path": "Path",
  "workspaces.column.port": "Port",
  "workspaces.column.ttl": "TTL",
  "workspaces.create.action": "New workspace",
  "workspaces.create.title": "Create workspace",
  "workspaces.create.description":
    "Choose a workspace template, then provide the inputs it declares.",
  "workspaces.create.template": "Workspace template",
  "workspaces.create.templatePlaceholder": "Select a workspace template…",
  "workspaces.create.name": "Name",
  "workspaces.create.nameDescription":
    "Optional. Leave blank to use the template's naming rule.",
  "workspaces.create.ttl": "TTL",
  "workspaces.create.ttlDescription":
    "Optional lifetime, such as 30m, 2h, or 7d.",
  "workspaces.create.submit": "Create workspace",
  "workspaces.create.submitting": "Creating workspace…",
  "workspaces.create.cancel": "Cancel",
  "workspaces.create.required": "This input is required.",
  "workspaces.create.validationFailed":
    "Check the highlighted template inputs.",
  "workspaces.create.failed": "Could not create workspace.",
  "workspaces.syncBase": "Sync base",
  "workspaces.destroy": "Destroy",
  "workspaces.destroy.confirm.title": "Destroy workspace?",
  "workspaces.destroy.confirm.body":
    "“{name}” will be destroyed — its files are removed and this cannot be undone.",
  "workspaces.detail.notFound": "Workspace not found",
  "workspaces.detail.overview": "Overview",
  "workspaces.detail.expiresAt": "Expires",
  "workspaces.detail.mcp": "Playwright MCP",
  "workspaceSources.title": "Sources",
  "workspaceSources.empty": "No sources.",
  "workspaceSources.column.slot": "Slot",
  "workspaceSources.column.state": "State",
  "workspaceSources.column.branch": "Branch",
  "workspaceSources.column.drift": "Drift",
  "workspaceSources.column.path": "Path",

  // Sources.
  "sources.record": "Source",
  "sources.loading": "Loading sources…",
  "sources.empty": "No sources.",
  "sources.column.name": "Name",
  "sources.column.kind": "Kind",
  "sources.column.status": "Status",
  "sources.column.branch": "Branch",
  "sources.column.aheadBehind": "Ahead/Behind",
  "sources.column.dirty": "Working tree",
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

  // Operations.
  "operations.empty": "No jobs.",
  "operations.column.name": "Name",
  "operations.column.runtime": "Runtime",
  "operations.run": "Run",
  "operations.startFailed": "Could not start the job.",
  "operations.previewFailed": "Could not preview affected services.",
  "operations.runAndRestart": "Run and restart dependents",
  "operations.restart.confirm.title": "Run job and restart dependents?",
  "operations.restart.confirm.body": "This will run the job, then restart: {affected}.",
  "operations.stack.title": "Stack lifecycle",
  "operations.stack.build": "Build",
  "operations.stack.up": "Up",
  "operations.stack.down": "Down",
  "operations.stack.destroy": "Destroy",
  "operations.stack.destroy.confirm.title": "Destroy stack?",
  "operations.stack.destroy.confirm.body":
    "All services and runtime state are removed. This cannot be undone.",

  // Templates.
  "templates.empty.title": "No templates",
  "templates.column.name": "Template",
  "templates.column.kind": "Kind",
  "templates.column.path": "Path",
  "templates.inputs": "Inputs",

  // Secrets.
  "secrets.empty": "No declared secrets.",
  "secrets.column.name": "Name",
  "secrets.column.declared": "Declared",
  "secrets.column.hasValue": "Has value",
  "secrets.column.required": "Required",
  "secrets.column.envVar": "Env var",
  "secrets.required": "Required",
  "secrets.generated": "Generated",
  "secrets.protectedHint": "A required or generated secret cannot be deleted.",
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
  "secrets.set.failed": "The secret could not be set.",
};

// A translator bound to the `operator` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useOperatorT = createNamespaceT("operator", enOperatorMessages);
