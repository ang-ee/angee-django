import type { BaseAddonRoute, BaseMenuItem } from "@angee/base";
import { defineBaseAddon } from "@angee/base";
import {
  Cable,
  FolderGit2,
  GitBranch,
  GitFork,
  LayoutTemplate,
  Link2,
  Store,
  Webhook,
} from "lucide-react";

import { credentialCreateForm } from "./connect/credential-form";
import { OAuthConnectCallbackPage } from "./connect/OAuthConnectCallbackPage";
import { CredentialsPage } from "./connect/views/CredentialsPage";
import { ExternalAccountsPage } from "./connect/views/ExternalAccountsPage";
import { ProvidersPage } from "./connect/views/ProvidersPage";
import {
  CONNECT_CALLBACK_FALLBACK_PATH,
  CONNECT_CALLBACK_PATH,
} from "./connect/redirects";
import { enIntegrateMessages } from "./i18n";
import { IntegrationsPage } from "./views/IntegrationsPage";
import { RepositoriesPage } from "./views/RepositoriesPage";
import { SourcesPage } from "./views/SourcesPage";
import { TemplatesPage } from "./views/TemplatesPage";
import { VcsBridgesPage } from "./views/VcsBridgesPage";
import { VendorsPage } from "./views/VendorsPage";
import { WebhooksPage } from "./views/WebhooksPage";

const INTEGRATE_ID = "integrate";

const integrateRoutes: readonly BaseAddonRoute[] = [
  {
    // No `menu:` — the route is referenced by exactly one menu item (the
    // Integrations child), so chrome derivation needs no disambiguation.
    name: "integrate.integrations",
    path: "/integrate",
    shell: "console",
    component: IntegrationsPage,
    model: "Integration",
  },
  {
    // The integration record nests under the list; `IntegrationsPage` reads the
    // `$id` param and swaps to the detail form, so this route carries only the URL.
    name: "integrate.integration",
    path: "/integrate/$id",
    shell: "console",
    parent: "integrate.integrations",
  },
  {
    // Static `/integrate/vendors` outranks the `/integrate/$id` param route.
    name: "integrate.vendors",
    path: "/integrate/vendors",
    shell: "console",
    component: VendorsPage,
    model: "Vendor",
  },
  {
    name: "integrate.vendor",
    path: "/integrate/vendors/$id",
    shell: "console",
    parent: "integrate.vendors",
  },
  {
    name: "integrate.webhooks",
    path: "/integrate/webhooks",
    shell: "console",
    component: WebhooksPage,
    model: "WebhookSubscription",
  },
  {
    name: "integrate.webhook",
    path: "/integrate/webhooks/$id",
    shell: "console",
    parent: "integrate.webhooks",
  },
  {
    // Static `/integrate/vcs` outranks the `/integrate/$id` param route, like
    // vendors/webhooks.
    name: "integrate.vcs",
    path: "/integrate/vcs",
    shell: "console",
    component: VcsBridgesPage,
    model: "VcsBridge",
  },
  {
    name: "integrate.vcsBridge",
    path: "/integrate/vcs/$id",
    shell: "console",
    parent: "integrate.vcs",
  },
  {
    name: "integrate.repositories",
    path: "/integrate/repositories",
    shell: "console",
    component: RepositoriesPage,
    model: "Repository",
  },
  {
    name: "integrate.repository",
    path: "/integrate/repositories/$id",
    shell: "console",
    parent: "integrate.repositories",
  },
  {
    name: "integrate.sources",
    path: "/integrate/sources",
    shell: "console",
    component: SourcesPage,
    model: "Source",
  },
  {
    name: "integrate.source",
    path: "/integrate/sources/$id",
    shell: "console",
    parent: "integrate.sources",
  },
  {
    name: "integrate.templates",
    path: "/integrate/templates",
    shell: "console",
    component: TemplatesPage,
    model: "Template",
  },
  {
    name: "integrate.template",
    path: "/integrate/templates/$id",
    shell: "console",
    parent: "integrate.templates",
  },

  // --- Connect surface (outbound OAuth) -----------------------------------
  // The account-connect callback: the provider redirects back here after the user
  // approves. It stays on the authenticated `console` shell (unlike the public
  // sign-in callback) because the connect flow's actor is an already-signed-in
  // admin linking an outbound account — there is no pre-session bootstrap.
  {
    name: "integrate.connect.callback",
    path: CONNECT_CALLBACK_PATH,
    shell: "console",
    component: OAuthConnectCallbackPage,
  },
  {
    name: "integrate.connect.callbackFallback",
    path: CONNECT_CALLBACK_FALLBACK_PATH,
    shell: "console",
    component: OAuthConnectCallbackPage,
  },
  {
    name: "integrate.providers",
    path: "/integrate/providers",
    shell: "console",
    component: ProvidersPage,
    model: "OAuthClient",
  },
  {
    name: "integrate.provider",
    path: "/integrate/providers/$id",
    shell: "console",
    parent: "integrate.providers",
  },
  {
    name: "integrate.accounts",
    path: "/integrate/accounts",
    shell: "console",
    component: ExternalAccountsPage,
    model: "ExternalAccount",
  },
  {
    name: "integrate.account",
    path: "/integrate/accounts/$id",
    shell: "console",
    parent: "integrate.accounts",
  },
  {
    name: "integrate.credentials",
    path: "/integrate/credentials",
    shell: "console",
    component: CredentialsPage,
    model: "Credential",
  },
  {
    name: "integrate.credential",
    path: "/integrate/credentials/$id",
    shell: "console",
    parent: "integrate.credentials",
  },
];

const integrateMenu: readonly BaseMenuItem[] = [
  {
    // Route-less app root: the rail icon inherits its target from the first
    // child (Integrations), avoiding a duplicate route reference.
    id: INTEGRATE_ID,
    label: "Integrations",
    icon: "integrate",
    group: "platform",
    children: [
      {
        // Product connection records and their supporting catalogue.
        id: "integrate.integrations.group",
        label: "Integrations",
        icon: "integration",
        children: [
          { id: "integrate.integrations", label: "Integrations", icon: "integration", route: "integrate.integrations" },
          { id: "integrate.vendors", label: "Vendors", icon: "vendor", route: "integrate.vendors" },
          { id: "integrate.webhooks", label: "Webhooks", icon: "webhook", route: "integrate.webhooks" },
        ],
      },
      {
        // Repository/source inventory hangs off VCS-capable integrations.
        id: "integrate.sources.group",
        label: "Sources",
        icon: "source",
        children: [
          { id: "integrate.sources", label: "Sources", icon: "source", route: "integrate.sources" },
          { id: "integrate.templates", label: "Templates", icon: "integrateTemplate", route: "integrate.templates" },
          { id: "integrate.repositories", label: "Repositories", icon: "repository", route: "integrate.repositories" },
          { id: "integrate.vcs", label: "VCS Bridges", icon: "vcs", route: "integrate.vcs" },
        ],
      },
      {
        // OAuth client setup and the external identities those clients discover.
        id: "integrate.oauth.group",
        label: "OAuth",
        icon: "auth",
        children: [
          { id: "integrate.providers", label: "OAuth Providers", route: "integrate.providers", icon: "auth" },
          { id: "integrate.accounts", label: "External Accounts", route: "integrate.accounts", icon: "users" },
        ],
      },
      { id: "integrate.credentials", label: "Credentials", route: "integrate.credentials", icon: "check" },
    ],
  },
];

const integrate = defineBaseAddon({
  id: INTEGRATE_ID,
  routes: integrateRoutes,
  menus: integrateMenu,
  i18n: { integrate: enIntegrateMessages },
  // The credential CRUD form: used by the Credentials page "New" and the
  // relation-picker inline create (e.g. an Integration's credential field).
  forms: {
    Credential: credentialCreateForm,
  },
  icons: {
    integrate: Cable,
    integration: Link2,
    vendor: Store,
    webhook: Webhook,
    vcs: GitFork,
    repository: FolderGit2,
    source: GitBranch,
    integrateTemplate: LayoutTemplate,
  },
});

export {
  canConnectRecord,
  ConnectOAuthButton,
  parseManualCode,
  type OAuthConnectPayload,
} from "./connect/ConnectOAuthButton";
export {
  CONNECT_CALLBACK_FALLBACK_PATH,
  CONNECT_CALLBACK_PATH,
  connectCallbackPathForRecord,
  connectCallbackRedirectUri,
  currentConnectCallbackRedirectUri,
} from "./connect/redirects";

export default integrate;
