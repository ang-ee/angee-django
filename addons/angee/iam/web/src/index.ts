import { AUTH_LOGIN_METHOD_SLOT } from "@angee/app/auth";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { formViewSectionsSlot, type BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { createElement } from "react";

import { enIamMessages } from "./i18n";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import { LOGIN_CALLBACK_PATH } from "./redirects";
import { oidcLoginSection } from "./views/oidc-section";

export {
  IamLoginPage,
  IAM_LOGIN_BACKGROUND_IMAGE_URLS,
  type IamLoginPageProps,
} from "./IamLoginPage";

// One top-bar dropdown ("Roles" gathers the REBAC views) plus the "OIDC Providers"
// item — the inbound sign-in provider admin (the OAuth login refinement; the OAuth
// connect substrate moved to `@angee/integrate`). `TopMenu` renders a menu item with
// children as a dropdown; a route-less parent inherits its first child's target.
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
      {
        id: "iam.users.group",
        label: "Users",
        icon: "users",
        children: [
          { id: "iam.users", label: "Users", route: "iam.users", icon: "users" },
          { id: "iam.groups", label: "Groups", route: "iam.groups", icon: "users" },
        ],
      },
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
    ],
  },
];

const iam = defineBaseAddon({
  id: "iam",
  routes: [
    {
      name: "iam.login.callback",
      path: LOGIN_CALLBACK_PATH,
      layout: "public",
      component: lazyRouteComponent(() => import("./OAuthCallbackPage"), "OAuthCallbackPage"),
    },
    { name: "iam.overview", path: "/iam", component: lazyRouteComponent(() => import("./views/OverviewPage"), "OverviewPage") },
    ...resourcePageRoutes("iam.users", "/iam/users", lazyRouteComponent(() => import("./views/UsersPage"), "UsersPage"), "User"),
    { name: "iam.roles", path: "/iam/roles", resource: "iam.Role", component: lazyRouteComponent(() => import("./views/RolesPage"), "RolesPage") },
    ...resourcePageRoutes("iam.groups", "/iam/groups", lazyRouteComponent(() => import("./views/GroupsPage"), "GroupsPage"), "iam.Group"),
    { name: "iam.grants", path: "/iam/grants", resource: "iam.Grant", component: lazyRouteComponent(() => import("./views/GrantsPage"), "GrantsPage") },
    { name: "iam.relationships", path: "/iam/relationships", resource: "iam.Relationship", component: lazyRouteComponent(() => import("./views/RelationshipsPage"), "RelationshipsPage") },
    { name: "iam.schema", path: "/iam/schema", component: lazyRouteComponent(() => import("./views/SchemaPage"), "SchemaPage") },
  ],
  menus: identityMenu,
  i18n: { iam: enIamMessages },
  slots: [
    {
      slot: AUTH_LOGIN_METHOD_SLOT,
      id: "iam.oauth-login",
      content: createElement(OAuthLoginMethods),
    },
    {
      // OIDC login lives on the OAuth client itself; this contributes the OIDC tab
      // into integrate's OAuth-client form, gated to the OIDC provider types this
      // addon owns. No separate OIDC page/model — it's the same OAuthClient row.
      slot: formViewSectionsSlot("OAuthClient"),
      id: "iam.oidc-login",
      content: oidcLoginSection,
    },
  ],
});

export default iam;
