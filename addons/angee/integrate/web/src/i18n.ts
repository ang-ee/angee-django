import { createNamespaceT } from "@angee/ui";

// English fallback strings for the integrate addon's hard-coded UI copy. The host
// runtime owns the active translations; these are the defaults used when a key is
// missing — model-driven `<Column>`/`<Field>` labels stay metadata-driven and are
// not listed here. Keys are dotted within the `integrate` namespace.
export const enIntegrateMessages: Record<string, string> = {
  // Shared action verbs reused across the model-driven pages.
  "action.syncNow": "Sync now",
  "action.refresh": "Refresh",

  // Shared list/group column labels.
  "col.status": "Status",
  "col.lifecycle": "Lifecycle",
  "col.runtimeStatus": "Runtime",
  "col.implementation": "Implementation",
  "col.type": "Type",
  "col.vendor": "Vendor",
  "col.credential": "Credential",
  "col.lastError": "Last error",
  "col.source": "Source",
  "col.repository": "Repository",
  "col.vcsBridge": "VCS bridge",

  // Integrations page.
  "integrations.identity": "Identity",
  "integrations.authentication": "Authentication",
  "integrations.runtime": "Runtime",
  "integrations.inference": "Inference provider",
  "integrations.vcs": "VCS bridge",
  "integrations.implClass": "Implementation",
  "integrations.typeGroup": "Type",
  "integrations.action.connect": "Connect",
  "integrations.connect.startError": "Could not start integration connection.",
  "integrations.connect.connected": "Integration connected.",

  // VCS bridge page.
  "vcs.backendClass": "Backend",
  "vcs.discover": "Discover repositories",
  "bridge.group.sync": "Sync",

  // Repositories page detail groups.
  "repositories.repository": "Repository",
  "repositories.remote": "Remote",

  // Sources page detail group.
  "sources.pointer": "Pointer",

  // Templates page detail groups.
  "templates.title": "Templates",
  "templates.template": "Template",
  "templates.source": "Source",
  "templateSources.title": "Template sources",
  "templateSources.pointer": "Pointer",
  "templateSources.sync": "Sync templates",

  // Webhooks page.
  "webhooks.filters": "Filters",
  "webhooks.sendTest": "Send test event",
  "webhooks.rotateSecret": "Rotate secret",
  "webhooks.rotateFailed": "Could not rotate the signing secret.",
  "webhooks.newSecretTitle": "New signing secret",
  "webhooks.newSecretBody": "Copy this now — it is shown only once.",
  "webhooks.signingSecret": "Signing secret",
  "webhooks.rotated": "Signing secret rotated.",

  // Add-repository dialog.
  "addRepo.title": "Add repository",
  "addRepo.description":
    "Pick a VCS bridge, then type to find a repository to inventory.",
  "addRepo.integrationLabel": "VCS bridge",
  "addRepo.integrationPlaceholder": "Select a bridge",
  "addRepo.integrationSearch": "Search bridges...",
  "addRepo.nameLabel": "Repository name",
  "addRepo.namePlaceholder": "Type a repository name…",
  "addRepo.addFailed": "Could not add repository.",
  "addRepo.selectIntegration":
    "Select a bridge to search its repositories.",
  "addRepo.typeToSearch": "Type a repository name to search.",
  "addRepo.searching": "Searching…",
  "addRepo.noMatches": "No matching repositories.",
  "addRepo.added": "Added",

  // --- Connect surface (outbound OAuth: providers, accounts, credentials) ---
  // Cohesive block; relocatable to a future `iam_integrate_oidc/web` as a unit.
  // OAuth providers page — form-section labels and actions.
  "providers.group.client": "Client",
  "providers.group.endpoints": "Endpoints",
  "providers.group.behavior": "Behavior",
  "providers.group.scopes": "Scopes",
  "providers.group.claims": "Claims",
  "providers.group.oauthMetadata": "OAuth metadata",
  "providers.action.connect": "Connect account",
  "providers.action.discover": "Discover endpoints",
  "providers.discover.done": "Discovered endpoints.",
  "providers.discover.failed": "Discovery failed.",
  "providers.connect.startError": "Could not start account connection.",
  "providers.connect.redirecting": "Redirecting...",
  "providers.connect.openAuthorize": "Open the authorization page",
  "providers.connect.instructions": ", approve, then paste the code it shows below.",
  "providers.connect.codeLabel": "Authorization code",
  "providers.connect.codePlaceholder": "code#state",
  "providers.connect.codeIncomplete":
    "That code looks incomplete — paste the full value the page showed.",
  "providers.connect.codeMismatch":
    "That code is from a different attempt — start the connection again.",
  "providers.connect.stateIncomplete":
    "Connection state is incomplete — start the connection again.",
  "providers.connect.connected": "Account connected.",

  // Credentials page — form-section labels and actions.
  "credentials.group.health": "Health",
  "credentials.action.refresh": "Refresh token",
  "credentials.refresh.done": "Token refreshed.",
  "credentials.action.reveal": "Reveal secret",
  "credentials.reveal.noSecret": "This credential has no stored secret to reveal.",
  "credentials.reveal.title": "Credential secret",
  "credentials.reveal.body":
    "Copy it now — it is shown on request only and never kept in the form.",
  "credentials.reveal.secretLabel": "Secret",

  // External accounts page — form-section labels.
  "externalAccounts.group.identity": "Identity",
  "externalAccounts.provider": "Provider",

  // Account-connect callback page.
  "connectCallback.completing": "Connecting account...",
  "connectCallback.confirming": "Your account connection is being confirmed.",
  "connectCallback.failedTitle": "Could not connect account",
  "connectCallback.backToProviders": "Back to providers",
  "connectCallback.browserOnly":
    "The account callback can only be completed in a browser.",
  "connectCallback.missingInfo":
    "The account callback is missing required information.",
  "connectCallback.completeError": "Could not connect account.",
};

// A translator bound to the `integrate` namespace: resolves against the host
// runtime's merged i18n first, then falls back to the bundled English. Thin alias
// over the shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useIntegrateT = createNamespaceT("integrate", enIntegrateMessages);
