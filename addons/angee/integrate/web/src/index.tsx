import type { BaseMenuItem } from "@angee/ui";
import { defineBaseAddon, type BaseAddonRoute } from "@angee/app";
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
import { CONNECT_CALLBACK_PATH } from "./connect/redirects";
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
  // List/detail pairs: the list route owns the component/model, and the `$id`
  // child carries only the nested record URL.
  ...consoleRecordRoutes("integrate.integrations", "integrate.integration", "/integrate", IntegrationsPage, "Integration"),
  ...consoleRecordRoutes("integrate.vendors", "integrate.vendor", "/integrate/vendors", VendorsPage, "Vendor"),
  ...consoleRecordRoutes("integrate.webhooks", "integrate.webhook", "/integrate/webhooks", WebhooksPage, "WebhookSubscription"),
  ...consoleRecordRoutes("integrate.vcs", "integrate.vcsBridge", "/integrate/vcs", VcsBridgesPage, "VcsBridge"),
  ...consoleRecordRoutes("integrate.repositories", "integrate.repository", "/integrate/repositories", RepositoriesPage, "Repository"),
  ...consoleRecordRoutes("integrate.sources", "integrate.source", "/integrate/sources", SourcesPage, "Source"),
  ...consoleRecordRoutes("integrate.templates", "integrate.template", "/integrate/templates", TemplatesPage, "Template"),

  // --- Connect surface (outbound OAuth) -----------------------------------
  // The account-connect callback: the provider redirects back here after the user
  // approves. It stays on the authenticated `console` layout (unlike the public
  // sign-in callback) because the connect flow's actor is an already-signed-in
  // admin linking an outbound account — there is no pre-session bootstrap.
  {
    name: "integrate.connect.callback",
    path: CONNECT_CALLBACK_PATH,
    layout: "console",
    component: OAuthConnectCallbackPage,
  },
  ...consoleRecordRoutes("integrate.providers", "integrate.provider", "/integrate/providers", ProvidersPage, "OAuthClient"),
  ...consoleRecordRoutes("integrate.accounts", "integrate.account", "/integrate/accounts", ExternalAccountsPage, "ExternalAccount"),
  ...consoleRecordRoutes("integrate.credentials", "integrate.credential", "/integrate/credentials", CredentialsPage, "Credential"),
];

function consoleRecordRoutes(
  name: string,
  detailName: string,
  path: string,
  component: BaseAddonRoute["component"],
  resource: string,
): readonly BaseAddonRoute[] {
  return [
    { name, path, layout: "console", component, resource },
    { name: detailName, path: `${path}/$id`, layout: "console", parent: name },
  ];
}

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
          { id: "integrate.templates", label: "Templates", icon: "integrate-template", route: "integrate.templates" },
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
    "integrate-template": LayoutTemplate,
  },
});

export {
  canConnectRecord,
  ConnectOAuthButton,
  parseManualCode,
  type OAuthConnectPayload,
} from "./connect/ConnectOAuthButton";
export {
  CONNECT_CALLBACK_PATH,
  connectCallbackRedirectUri,
  currentConnectCallbackRedirectUri,
} from "./connect/redirects";

export default integrate;
