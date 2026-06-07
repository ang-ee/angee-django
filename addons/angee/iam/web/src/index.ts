import {
  AUTH_LOGIN_METHOD_SLOT,
  type BaseAddon,
  type BaseMenuItem,
} from "@angee/base";
import { createElement } from "react";

import { OAuthCallbackPage } from "./OAuthCallbackPage";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import { LEGACY_LOGIN_CALLBACK_PATH, LOGIN_CALLBACK_PATH } from "./redirects";
import { ConnectionsPage } from "./views/ConnectionsPage";
import { GrantsPage } from "./views/GrantsPage";
import { OverviewPage } from "./views/OverviewPage";
import { RelationshipsPage } from "./views/RelationshipsPage";
import { RolesPage } from "./views/RolesPage";
import { SchemaPage } from "./views/SchemaPage";
import { UsersPage } from "./views/UsersPage";

const identityMenu: readonly BaseMenuItem[] = [
  {
    id: "iam",
    label: "Identity",
    icon: "auth",
    group: "platform",
    children: [
      { label: "Overview", route: "iam.overview", icon: "home" },
      { label: "Users", route: "iam.users", icon: "users" },
      { label: "Roles", route: "iam.roles", icon: "auth" },
      { label: "Grants", route: "iam.grants", icon: "check" },
      {
        label: "Relationships",
        route: "iam.relationships",
        icon: "share",
      },
      { label: "Schema", route: "iam.schema", icon: "columns" },
      {
        label: "Connections",
        route: "iam.connections",
        icon: "grid",
      },
    ],
  },
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
      name: "iam.overview",
      path: "/iam",
      shell: "console",
      component: OverviewPage,
    },
    {
      name: "iam.users",
      path: "/iam/users",
      shell: "console",
      component: UsersPage,
    },
    {
      name: "iam.roles",
      path: "/iam/roles",
      shell: "console",
      component: RolesPage,
    },
    {
      name: "iam.grants",
      path: "/iam/grants",
      shell: "console",
      component: GrantsPage,
    },
    {
      name: "iam.relationships",
      path: "/iam/relationships",
      shell: "console",
      component: RelationshipsPage,
    },
    {
      name: "iam.schema",
      path: "/iam/schema",
      shell: "console",
      component: SchemaPage,
    },
    {
      name: "iam.connections",
      path: "/iam/connections",
      shell: "console",
      component: ConnectionsPage,
    },
  ],
  menus: identityMenu,
  slots: [
    {
      slot: AUTH_LOGIN_METHOD_SLOT,
      id: "iam.oauth-login",
      content: createElement(OAuthLoginMethods),
    },
  ],
};

export default iam;
