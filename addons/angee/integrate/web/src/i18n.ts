import { useNamespaceT, type MessageVars } from "@angee/sdk";

// English fallback strings for the integrate addon's hard-coded UI copy. The host
// runtime owns the active translations; these are the defaults used when a key is
// missing — model-driven `<Column>`/`<Field>` labels stay metadata-driven and are
// not listed here. Keys are dotted within the `integrate` namespace.
export const enIntegrateMessages: Record<string, string> = {
  // Shared action verbs reused across the model-driven pages.
  "integrate.action.syncNow": "Sync now",
  "integrate.action.refresh": "Refresh",
  "integrate.action.disable": "Disable",

  // Integrations page.
  "integrate.integrations.authentication": "Authentication",
  "integrate.integrations.testConnection": "Test connection",
  "integrate.integrations.activate": "Activate",

  // VCS integrations page.
  "integrate.vcs.discover": "Discover repositories",

  // Repositories page detail groups.
  "integrate.repositories.repository": "Repository",
  "integrate.repositories.remote": "Remote",

  // Sources page detail group.
  "integrate.sources.pointer": "Pointer",

  // Webhooks page.
  "integrate.webhooks.filters": "Filters",
  "integrate.webhooks.sendTest": "Send test event",
  "integrate.webhooks.rotateSecret": "Rotate secret",
  "integrate.webhooks.enable": "Enable",
  "integrate.webhooks.rotateFailed": "Could not rotate the signing secret.",
  "integrate.webhooks.newSecretTitle": "New signing secret",
  "integrate.webhooks.newSecretBody": "Copy this now — it is shown only once.",
  "integrate.webhooks.signingSecret": "Signing secret",
  "integrate.webhooks.rotated": "Signing secret rotated.",

  // Add-repository dialog.
  "integrate.addRepo.title": "Add repository",
  "integrate.addRepo.description":
    "Pick a VCS integration, then type to find a repository to inventory.",
  "integrate.addRepo.integrationLabel": "VCS integration",
  "integrate.addRepo.integrationPlaceholder": "Select an integration",
  "integrate.addRepo.integrationSearch": "Search integrations…",
  "integrate.addRepo.nameLabel": "Repository name",
  "integrate.addRepo.namePlaceholder": "Type a repository name…",
  "integrate.addRepo.addFailed": "Could not add repository.",
  "integrate.addRepo.selectIntegration":
    "Select an integration to search its repositories.",
  "integrate.addRepo.typeToSearch": "Type a repository name to search.",
  "integrate.addRepo.searching": "Searching…",
  "integrate.addRepo.noMatches": "No matching repositories.",
  "integrate.addRepo.added": "Added",
};

// A translator bound to the `integrate` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useIntegrateT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("integrate", enIntegrateMessages);
}
