import type { BaseMenuItem } from "@angee/base";
import { useNamespaceT, type I18nResources, type MessageVars } from "@angee/sdk";

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

/**
 * The operator console's copy beyond the section titles (those come from
 * `enOperatorBundleForMenu`): table headers, row/stack action labels, empty and
 * loading messages, confirm dialogs, card titles, and the set-secret form. Keyed
 * under the `operator` namespace and merged into the manifest bundle in `index.ts`.
 */
export const enOperatorMessages: Record<string, string> = {
  // Shared table chrome.
  "operator.table.actions": "Actions",

  // Shared live-log panel.
  "operator.logs.live": "Live",
  "operator.logs.connecting": "Connecting",
  "operator.logs.error": "Disconnected",
  "operator.logs.empty": "Waiting for log output…",

  // Transport / connection states.
  "operator.transport.connecting": "Connecting to operator",
  "operator.transport.unavailable.title": "Operator daemon unavailable",
  "operator.transport.unavailable.description":
    "Operator daemon is not configured for this user.",
  "operator.transport.noConsoleClient": 'No "console" GraphQL client is configured.',
  "operator.transport.unknownError": "Unknown operator error.",

  // Overview.
  "operator.overview.loading": "Loading overview",
  "operator.overview.stack.title": "Stack",
  "operator.overview.stack.empty": "No stack status.",
  "operator.overview.health.title": "Health",
  "operator.overview.health.empty": "No health report.",

  // Services.
  "operator.services.loading": "Loading services",
  "operator.services.empty": "No services.",
  "operator.services.column.name": "Name",
  "operator.services.column.runtime": "Runtime",
  "operator.services.column.status": "Status",
  "operator.services.column.health": "Health",
  "operator.services.start": "Start",
  "operator.services.restart": "Restart",
  "operator.services.recreate": "Recreate",
  "operator.services.stop": "Stop",
  "operator.services.destroy": "Destroy",
  "operator.services.destroy.confirm.title": "Destroy service?",
  "operator.services.destroy.confirm.body":
    "“{name}” will be stopped and removed from the stack — the workspace it mounts is left intact.",
  "operator.services.detail.notFound": "Service not found",
  "operator.services.detail.overview": "Overview",
  "operator.services.detail.endpoint": "Endpoint",
  "operator.services.detail.internal": "Internal address",
  "operator.services.detail.logs": "Logs",

  // Workspaces.
  "operator.workspaces.loading": "Loading workspaces",
  "operator.workspaces.empty": "No workspaces.",
  "operator.workspaces.column.name": "Name",
  "operator.workspaces.column.template": "Template",
  "operator.workspaces.column.path": "Path",
  "operator.workspaces.column.port": "Port",
  "operator.workspaces.column.ttl": "TTL",
  "operator.workspaces.syncBase": "Sync base",
  "operator.workspaces.destroy": "Destroy",
  "operator.workspaces.destroy.confirm.title": "Destroy workspace?",
  "operator.workspaces.destroy.confirm.body":
    "“{name}” will be destroyed — its files are removed and this cannot be undone.",
  "operator.workspaces.detail.notFound": "Workspace not found",
  "operator.workspaces.detail.overview": "Overview",
  "operator.workspaces.detail.expiresAt": "Expires",
  "operator.workspaces.detail.mcp": "Playwright MCP",
  "operator.workspaces.detail.logs": "Logs",

  // Sources.
  "operator.sources.loading": "Loading sources",
  "operator.sources.empty": "No sources.",
  "operator.sources.column.name": "Name",
  "operator.sources.column.kind": "Kind",
  "operator.sources.column.status": "Status",
  "operator.sources.column.branch": "Branch",
  "operator.sources.column.aheadBehind": "Ahead/Behind",
  "operator.sources.column.dirty": "Dirty",
  "operator.sources.dirty": "dirty",
  "operator.sources.clean": "clean",
  "operator.sources.fetch": "Fetch",
  "operator.sources.pull": "Pull",
  "operator.sources.push": "Push",
  "operator.sources.detail.notFound": "Source not found",
  "operator.sources.detail.overview": "Overview",
  "operator.sources.detail.path": "Path",
  "operator.sources.detail.upstream": "Upstream",
  "operator.sources.detail.currentRef": "Current ref",
  "operator.sources.detail.pushed": "Pushed",
  "operator.sources.detail.error": "Error",

  // GitOps.
  "operator.gitops.loading": "Loading GitOps topology",
  "operator.gitops.empty.title": "No GitOps topology",
  "operator.gitops.links.empty": "No GitOps links.",
  "operator.gitops.summary.clean": "Clean",
  "operator.gitops.summary.dirty": "Dirty",
  "operator.gitops.summary.ahead": "Ahead",
  "operator.gitops.summary.behind": "Behind",
  "operator.gitops.summary.diverged": "Diverged",
  "operator.gitops.summary.unpushed": "Unpushed",
  "operator.gitops.column.source": "Source",
  "operator.gitops.column.workspace": "Workspace",
  "operator.gitops.column.slot": "Slot",
  "operator.gitops.column.status": "Status",
  "operator.gitops.column.branch": "Branch",
  "operator.gitops.column.aheadBehind": "Ahead/Behind",
  "operator.gitops.column.pushed": "Pushed",
  "operator.gitops.pushed.yes": "yes",
  "operator.gitops.pushed.no": "no",

  // Operations.
  "operator.operations.loading": "Loading operations",
  "operator.operations.empty": "No jobs.",
  "operator.operations.column.name": "Name",
  "operator.operations.column.runtime": "Runtime",
  "operator.operations.run": "Run",
  "operator.operations.stack.title": "Stack lifecycle",
  "operator.operations.stack.build": "Build",
  "operator.operations.stack.up": "Up",
  "operator.operations.stack.down": "Down",
  "operator.operations.stack.destroy": "Destroy",
  "operator.operations.stack.destroy.confirm.title": "Destroy stack?",
  "operator.operations.stack.destroy.confirm.body":
    "All services and runtime state are removed. This cannot be undone.",

  // Templates.
  "operator.templates.loading": "Loading templates",
  "operator.templates.empty.title": "No templates",
  "operator.templates.column.name": "Template",
  "operator.templates.column.kind": "Kind",
  "operator.templates.column.path": "Path",
  "operator.templates.inputs": "Inputs",
  "operator.templates.input.required": "required",
  "operator.templates.input.optional": "optional",
  "operator.templates.input.immutable": "immutable",
  "operator.templates.input.generated": "generated",

  // Secrets.
  "operator.secrets.loading": "Loading secrets",
  "operator.secrets.empty": "No declared secrets.",
  "operator.secrets.column.name": "Name",
  "operator.secrets.column.declared": "Declared",
  "operator.secrets.column.hasValue": "Has value",
  "operator.secrets.column.required": "Required",
  "operator.secrets.column.envVar": "Env var",
  "operator.secrets.column.actions": "Actions",
  "operator.secrets.yes": "yes",
  "operator.secrets.no": "no",
  "operator.secrets.value.set": "set",
  "operator.secrets.value.empty": "empty",
  "operator.secrets.protected": "Protected",
  "operator.secrets.protected.hint":
    "Control-plane secret (required or generated) — cannot be deleted from the console.",
  "operator.secrets.delete": "Delete",
  "operator.secrets.delete.confirm.title": "Delete secret?",
  "operator.secrets.delete.confirm.body":
    "“{name}” will be removed from the secrets backend.",
  "operator.secrets.form.title": "Set a secret",
  "operator.secrets.form.name": "Name",
  "operator.secrets.form.namePlaceholder": "SECRET_NAME",
  "operator.secrets.form.value": "Value",
  "operator.secrets.form.valuePlaceholder": "value",
  "operator.secrets.form.submit": "Set",
  "operator.secrets.set.label": "Set secret",
  "operator.secrets.delete.label": "Delete secret",
};

// A translator bound to the `operator` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useOperatorT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("operator", enOperatorMessages);
}
