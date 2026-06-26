import { useNamespaceT } from "@angee/ui";
import type { MessageVars } from "@angee/refine";

// English fallback strings for the integrate addon's hard-coded UI copy. The host
// runtime owns the active translations; these are the defaults used when a key is
// missing — model-driven `<Column>`/`<Field>` labels stay metadata-driven and are
// not listed here. Keys are dotted within the `integrate` namespace.
export const enIntegrateMessages: Record<string, string> = {
  // Shared action verbs reused across the model-driven pages.
  "integrate.action.syncNow": "Sync now",
  "integrate.action.refresh": "Refresh",

  // Shared list/group column labels.
  "integrate.col.status": "Status",
  "integrate.col.implementation": "Implementation",
  "integrate.col.vendor": "Vendor",
  "integrate.col.credential": "Credential",
  "integrate.col.lastError": "Last error",

  // Integrations page.
  "integrate.integrations.identity": "Identity",
  "integrate.integrations.authentication": "Authentication",
  "integrate.integrations.runtime": "Runtime",
  "integrate.integrations.inference": "Inference provider",
  "integrate.integrations.vcs": "VCS bridge",
  "integrate.integrations.implClass": "Implementation",
  "integrate.integrations.action.connect": "Connect",
  "integrate.integrations.connect.startError": "Could not start integration connection.",
  "integrate.integrations.connect.connected": "Integration connected.",

  // VCS bridge page.
  "integrate.vcs.backendClass": "Backend",
  "integrate.vcs.discover": "Discover repositories",

  // Repositories page detail groups.
  "integrate.repositories.repository": "Repository",
  "integrate.repositories.remote": "Remote",

  // Sources page detail group.
  "integrate.sources.pointer": "Pointer",

  // Templates page detail groups.
  "integrate.templates.title": "Templates",
  "integrate.templates.template": "Template",
  "integrate.templates.source": "Source",
  "integrate.templateSources.title": "Template sources",
  "integrate.templateSources.pointer": "Pointer",
  "integrate.templateSources.sync": "Sync templates",

  // Webhooks page.
  "integrate.webhooks.filters": "Filters",
  "integrate.webhooks.sendTest": "Send test event",
  "integrate.webhooks.rotateSecret": "Rotate secret",
  "integrate.webhooks.rotateFailed": "Could not rotate the signing secret.",
  "integrate.webhooks.newSecretTitle": "New signing secret",
  "integrate.webhooks.newSecretBody": "Copy this now — it is shown only once.",
  "integrate.webhooks.signingSecret": "Signing secret",
  "integrate.webhooks.rotated": "Signing secret rotated.",

  // Add-repository dialog.
  "integrate.addRepo.title": "Add repository",
  "integrate.addRepo.description":
    "Pick a VCS bridge, then type to find a repository to inventory.",
  "integrate.addRepo.integrationLabel": "VCS bridge",
  "integrate.addRepo.integrationPlaceholder": "Select a bridge",
  "integrate.addRepo.integrationSearch": "Search bridges...",
  "integrate.addRepo.nameLabel": "Repository name",
  "integrate.addRepo.namePlaceholder": "Type a repository name…",
  "integrate.addRepo.addFailed": "Could not add repository.",
  "integrate.addRepo.selectIntegration":
    "Select a bridge to search its repositories.",
  "integrate.addRepo.typeToSearch": "Type a repository name to search.",
  "integrate.addRepo.searching": "Searching…",
  "integrate.addRepo.noMatches": "No matching repositories.",
  "integrate.addRepo.added": "Added",

  // --- Connect surface (outbound OAuth: providers, accounts, credentials) ---
  // Cohesive block; relocatable to a future `iam_integrate_oidc/web` as a unit.
  // OAuth providers page — form-section labels and actions.
  "integrate.providers.group.client": "Client",
  "integrate.providers.group.endpoints": "Endpoints",
  "integrate.providers.group.behavior": "Behavior",
  "integrate.providers.group.scopes": "Scopes",
  "integrate.providers.group.claims": "Claims",
  "integrate.providers.group.oauthMetadata": "OAuth metadata",
  "integrate.providers.action.connect": "Connect account",
  "integrate.providers.action.discover": "Discover endpoints",
  "integrate.providers.discover.done": "Discovered endpoints.",
  "integrate.providers.discover.failed": "Discovery failed.",
  "integrate.providers.connect.startError": "Could not start account connection.",
  "integrate.providers.connect.redirecting": "Redirecting...",
  "integrate.providers.connect.openAuthorize": "Open the authorization page",
  "integrate.providers.connect.instructions": ", approve, then paste the code it shows below.",
  "integrate.providers.connect.codeLabel": "Authorization code",
  "integrate.providers.connect.codePlaceholder": "code#state",
  "integrate.providers.connect.codeIncomplete":
    "That code looks incomplete — paste the full value the page showed.",
  "integrate.providers.connect.codeMismatch":
    "That code is from a different attempt — start the connection again.",
  "integrate.providers.connect.stateIncomplete":
    "Connection state is incomplete — start the connection again.",
  "integrate.providers.connect.connected": "Account connected.",

  // Credentials page — form-section labels and actions.
  "integrate.credentials.group.health": "Health",
  "integrate.credentials.action.reveal": "Reveal secret",
  "integrate.credentials.reveal.noSecret": "This credential has no stored secret to reveal.",
  "integrate.credentials.reveal.title": "Credential secret",
  "integrate.credentials.reveal.body":
    "Copy it now — it is shown on request only and never kept in the form.",
  "integrate.credentials.reveal.secretLabel": "Secret",

  // External accounts page — form-section labels.
  "integrate.externalAccounts.group.identity": "Identity",
  "integrate.externalAccounts.provider": "Provider",

  // Account-connect callback page.
  "integrate.connectCallback.completing": "Connecting account...",
  "integrate.connectCallback.confirming": "Your account connection is being confirmed.",
  "integrate.connectCallback.failedTitle": "Could not connect account",
  "integrate.connectCallback.backToProviders": "Back to providers",
  "integrate.connectCallback.browserOnly":
    "The account callback can only be completed in a browser.",
  "integrate.connectCallback.missingInfo":
    "The account callback is missing required information.",
  "integrate.connectCallback.completeError": "Could not connect account.",
};

// A translator bound to the `integrate` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useIntegrateT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("integrate", enIntegrateMessages);
}
