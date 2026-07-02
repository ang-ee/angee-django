import type { BaseMenuItem } from "@angee/ui";
import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
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
import {
  CONNECT_CALLBACK_LOOPBACK_PATH,
  CONNECT_CALLBACK_PATH,
} from "./connect/redirects";
import { enIntegrateMessages } from "./i18n";

const INTEGRATE_ID = "integrate";

// Both connect-callback routes render the same page — split it once and share.
const oauthConnectCallback = lazyRouteComponent(
  () => import("./connect/OAuthConnectCallbackPage"),
  "OAuthConnectCallbackPage",
);

const integrateRoutes: readonly BaseAddonRoute[] = [
  // List/detail pairs: the list route owns the component/model, and the `$id`
  // child carries only the nested record URL.
  ...resourcePageRoutes("integrate.integrations", "/integrate", lazyRouteComponent(() => import("./views/IntegrationsPage"), "IntegrationsPage"), "Integration", { detailName: "integrate.integration" }),
  ...resourcePageRoutes("integrate.vendors", "/integrate/vendors", lazyRouteComponent(() => import("./views/VendorsPage"), "VendorsPage"), "Vendor", { detailName: "integrate.vendor" }),
  ...resourcePageRoutes("integrate.webhooks", "/integrate/webhooks", lazyRouteComponent(() => import("./views/WebhooksPage"), "WebhooksPage"), "WebhookSubscription", { detailName: "integrate.webhook" }),
  ...resourcePageRoutes("integrate.vcs", "/integrate/vcs", lazyRouteComponent(() => import("./views/VcsBridgesPage"), "VcsBridgesPage"), "VcsBridge", { detailName: "integrate.vcsBridge" }),
  ...resourcePageRoutes("integrate.repositories", "/integrate/repositories", lazyRouteComponent(() => import("./views/RepositoriesPage"), "RepositoriesPage"), "Repository", { detailName: "integrate.repository" }),
  ...resourcePageRoutes("integrate.sources", "/integrate/sources", lazyRouteComponent(() => import("./views/SourcesPage"), "SourcesPage"), "Source", { detailName: "integrate.source" }),
  ...resourcePageRoutes("integrate.templates", "/integrate/templates", lazyRouteComponent(() => import("./views/TemplatesPage"), "TemplatesPage"), "Template", { detailName: "integrate.template" }),

  // --- Connect surface (outbound OAuth) -----------------------------------
  // The account-connect callback: the provider redirects back here after the user
  // approves. It stays on the authenticated `console` layout (unlike the public
  // sign-in callback) because the connect flow's actor is an already-signed-in
  // admin linking an outbound account — there is no pre-session bootstrap.
  {
    name: "integrate.connect.callback",
    path: CONNECT_CALLBACK_PATH,
    component: oauthConnectCallback,
  },
  // Loopback alias for fixed public clients (e.g. Anthropic) whose allow-list registers
  // only the bare `/callback` loopback: on localhost the backend rewrites the connect
  // redirect to this path (OAuthClient.loopback_redirect_path), so the provider returns
  // here. Same completion page — `currentConnectCallbackRedirectUri()` reflects the path.
  {
    name: "integrate.connect.callbackLoopback",
    path: CONNECT_CALLBACK_LOOPBACK_PATH,
    component: oauthConnectCallback,
  },
  ...resourcePageRoutes("integrate.providers", "/integrate/providers", lazyRouteComponent(() => import("./connect/views/ProvidersPage"), "ProvidersPage"), "OAuthClient", { detailName: "integrate.provider" }),
  ...resourcePageRoutes("integrate.accounts", "/integrate/accounts", lazyRouteComponent(() => import("./connect/views/ExternalAccountsPage"), "ExternalAccountsPage"), "ExternalAccount", { detailName: "integrate.account" }),
  ...resourcePageRoutes("integrate.credentials", "/integrate/credentials", lazyRouteComponent(() => import("./connect/views/CredentialsPage"), "CredentialsPage"), "Credential", { detailName: "integrate.credential" }),
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
  CONNECT_CALLBACK_LOOPBACK_PATH,
  CONNECT_CALLBACK_PATH,
  connectCallbackRedirectUri,
  currentConnectCallbackRedirectUri,
} from "./connect/redirects";

export default integrate;
export { VCS_BRIDGE_MODEL, VCS_BRIDGE_RELATION } from "./data/vcs-bridge";
