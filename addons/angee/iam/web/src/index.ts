import {
  AUTH_LOGIN_METHOD_SLOT,
  defineBaseAddon,
  formViewSectionsSlot,
  type BaseAddonRoute,
  type BaseMenuItem,
} from "@angee/base";
import { createElement } from "react";

import { enIamMessages } from "./i18n";
import { OAuthCallbackPage } from "./OAuthCallbackPage";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import { LOGIN_CALLBACK_PATH } from "./redirects";
import { GrantsPage } from "./views/GrantsPage";
import { oidcLoginSection } from "./views/oidc-section";
import { OverviewPage } from "./views/OverviewPage";
import { RelationshipsPage } from "./views/RelationshipsPage";
import { RolesPage } from "./views/RolesPage";
import { SchemaPage } from "./views/SchemaPage";
import { UsersPage } from "./views/UsersPage";

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
    ],
  },
];

// Each model page is a routed DataPage: a list route + a `$id` detail child the
// list page swaps to inline. `model` tags the collection route so relation fields
// targeting it can "follow" to this detail page.
const consolePage = (
  name: string,
  path: string,
  component: BaseAddonRoute["component"],
  model?: string,
): readonly BaseAddonRoute[] => [
  { name, path, shell: "console", component, ...(model ? { model } : {}) },
  { name: `${name}.record`, path: `${path}/$id`, shell: "console", parent: name },
];

const iam = defineBaseAddon({
  id: "iam",
  routes: [
    {
      name: "iam.login.callback",
      path: LOGIN_CALLBACK_PATH,
      shell: "public",
      component: OAuthCallbackPage,
    },
    { name: "iam.overview", path: "/iam", shell: "console", component: OverviewPage },
    ...consolePage("iam.users", "/iam/users", UsersPage, "User"),
    { name: "iam.roles", path: "/iam/roles", shell: "console", component: RolesPage },
    { name: "iam.grants", path: "/iam/grants", shell: "console", component: GrantsPage },
    { name: "iam.relationships", path: "/iam/relationships", shell: "console", component: RelationshipsPage },
    { name: "iam.schema", path: "/iam/schema", shell: "console", component: SchemaPage },
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
