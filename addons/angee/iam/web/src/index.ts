import {
  AUTH_LOGIN_METHOD_SLOT,
  type BaseAddon,
  type BaseAddonRoute,
  type BaseMenuItem,
} from "@angee/base";
import { createElement } from "react";

import { OAuthCallbackPage } from "./OAuthCallbackPage";
import { OAuthConnectCallbackPage } from "./OAuthConnectCallbackPage";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import {
  CONNECT_CALLBACK_PATH,
  LEGACY_CONNECT_CALLBACK_PATH,
  LEGACY_LOGIN_CALLBACK_PATH,
  LOGIN_CALLBACK_PATH,
} from "./redirects";
import { credentialCreateForm } from "./views/credential-form";
import { CredentialsPage } from "./views/CredentialsPage";
import { ExternalAccountsPage } from "./views/ExternalAccountsPage";
import { GrantsPage } from "./views/GrantsPage";
import { OverviewPage } from "./views/OverviewPage";
import { ProvidersPage } from "./views/ProvidersPage";
import { RelationshipsPage } from "./views/RelationshipsPage";
import { RolesPage } from "./views/RolesPage";
import { SchemaPage } from "./views/SchemaPage";
import { UsersPage } from "./views/UsersPage";

// Two top-bar dropdowns: "Roles" groups the REBAC views, "Federation" groups the
// third-party sign-in surfaces. `TopMenu` renders a menu item with children as a
// dropdown; a route-less parent inherits its first child's target.
const identityMenu: readonly BaseMenuItem[] = [
  {
    // Route-less app root: the rail icon inherits its target from the first
    // child (Overview), so `iam.overview` is referenced by exactly one menu item.
    id: "iam",
    label: "IAM",
    icon: "auth",
    group: "platform",
    children: [
      { id: "iam.overview", label: "Overview", route: "iam.overview", icon: "home" },
      { id: "iam.users", label: "Users", route: "iam.users", icon: "users" },
      {
        id: "iam.roles.group",
        label: "Roles",
        icon: "auth",
        children: [
          { id: "iam.roles", label: "Roles", route: "iam.roles", icon: "auth" },
          { id: "iam.grants", label: "Grants", route: "iam.grants", icon: "check" },
          { id: "iam.relationships", label: "Relationships", route: "iam.relationships", icon: "share" },
          { id: "iam.schema", label: "Schema", route: "iam.schema", icon: "columns" },
        ],
      },
      {
        id: "iam.federation",
        label: "Federation",
        icon: "grid",
        children: [
          { id: "iam.providers", label: "Providers", route: "iam.providers", icon: "auth" },
          { id: "iam.accounts", label: "External Accounts", route: "iam.accounts", icon: "users" },
          { id: "iam.credentials", label: "Credentials", route: "iam.credentials", icon: "check" },
        ],
      },
    ],
  },
];

// Each model page is a routed DataPage: a list route + a `$id` detail child the
// list page swaps to inline.
const consolePage = (name: string, path: string, component: BaseAddonRoute["component"]): readonly BaseAddonRoute[] => [
  { name, path, shell: "console", component },
  { name: `${name}.record`, path: `${path}/$id`, shell: "console", parent: name },
];

const iam: BaseAddon = {
  id: "iam",
  routes: [
    {
      name: "iam.login.callback",
      path: LOGIN_CALLBACK_PATH,
      shell: "public",
      component: OAuthCallbackPage,
    },
    {
      name: "iam.login.callback.legacy",
      path: LEGACY_LOGIN_CALLBACK_PATH,
      shell: "public",
      component: OAuthCallbackPage,
    },
    {
      name: "iam.connect.callback",
      path: CONNECT_CALLBACK_PATH,
      shell: "console",
      component: OAuthConnectCallbackPage,
    },
    {
      name: "iam.connect.callback.legacy",
      path: LEGACY_CONNECT_CALLBACK_PATH,
      shell: "console",
      component: OAuthConnectCallbackPage,
    },
    { name: "iam.overview", path: "/iam", shell: "console", component: OverviewPage },
    ...consolePage("iam.users", "/iam/users", UsersPage),
    { name: "iam.roles", path: "/iam/roles", shell: "console", component: RolesPage },
    { name: "iam.grants", path: "/iam/grants", shell: "console", component: GrantsPage },
    { name: "iam.relationships", path: "/iam/relationships", shell: "console", component: RelationshipsPage },
    { name: "iam.schema", path: "/iam/schema", shell: "console", component: SchemaPage },
    ...consolePage("iam.providers", "/iam/providers", ProvidersPage),
    ...consolePage("iam.accounts", "/iam/accounts", ExternalAccountsPage),
    ...consolePage("iam.credentials", "/iam/credentials", CredentialsPage),
  ],
  menus: identityMenu,
  // The credential CRUD form: used by the Credentials page "New" and the
  // relation-picker inline create (e.g. an Integration's credential field).
  forms: {
    Credential: credentialCreateForm,
  },
  slots: [
    {
      slot: AUTH_LOGIN_METHOD_SLOT,
      id: "iam.oauth-login",
      content: createElement(OAuthLoginMethods),
    },
  ],
};

export default iam;
