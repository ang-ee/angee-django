import {
  AUTH_LOGIN_METHOD_SLOT,
  type BaseAddon,
  type ChromeMenuItem,
} from "@angee/base";
import { createElement } from "react";

import { OAuthCallbackPage } from "./OAuthCallbackPage";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import { ConnectionsPage } from "./views/ConnectionsPage";
import { GrantsPage } from "./views/GrantsPage";
import { OverviewPage } from "./views/OverviewPage";
import { RelationshipsPage } from "./views/RelationshipsPage";
import { RolesPage } from "./views/RolesPage";
import { SchemaPage } from "./views/SchemaPage";
import { UsersPage } from "./views/UsersPage";

const identityMenu: readonly ChromeMenuItem[] = [
  {
    id: "iam",
    label: "Identity",
    icon: "auth",
    group: "platform",
    children: [
      { id: "iam.overview", label: "Overview", to: "/iam", icon: "home" },
      { id: "iam.users", label: "Users", to: "/iam/users", icon: "users" },
      { id: "iam.roles", label: "Roles", to: "/iam/roles", icon: "auth" },
      { id: "iam.grants", label: "Grants", to: "/iam/grants", icon: "check" },
      {
        id: "iam.relationships",
        label: "Relationships",
        to: "/iam/relationships",
        icon: "share",
      },
      { id: "iam.schema", label: "Schema", to: "/iam/schema", icon: "columns" },
      {
        id: "iam.connections",
        label: "Connections",
        to: "/iam/connections",
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
      path: "/login/callback",
      shell: "public",
      component: OAuthCallbackPage,
    },
    {
      name: "iam.overview",
      path: "/iam",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Overview" }],
      component: OverviewPage,
    },
    {
      name: "iam.users",
      path: "/iam/users",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Users" }],
      component: UsersPage,
    },
    {
      name: "iam.roles",
      path: "/iam/roles",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Roles" }],
      component: RolesPage,
    },
    {
      name: "iam.grants",
      path: "/iam/grants",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Grants" }],
      component: GrantsPage,
    },
    {
      name: "iam.relationships",
      path: "/iam/relationships",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Relationships" }],
      component: RelationshipsPage,
    },
    {
      name: "iam.schema",
      path: "/iam/schema",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Schema" }],
      component: SchemaPage,
    },
    {
      name: "iam.connections",
      path: "/iam/connections",
      shell: "console",
      title: "Identity",
      icon: "auth",
      breadcrumbs: [{ label: "Identity" }, { label: "Connections" }],
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
