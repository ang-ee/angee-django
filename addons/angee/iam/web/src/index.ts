import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { createElement } from "react";

import {
  ACCOUNT_ACTION_FIELDS,
  IssuePasswordRecordAction,
  RenameRecordAction,
  ResetPasswordRecordAction,
  SetActiveRecordActions,
} from "./account-actions";
import { ShareListChrome, ShareRecordChrome } from "./ShareAccess";
import { enIamMessages } from "./i18n";
import { OAuthLoginMethods } from "./OAuthLoginMethods";
import { LOGIN_CALLBACK_PATH } from "./redirects";
import { subjectsWidget } from "./assignment-subject-widget";
import { USER_LIST_COLUMNS, USER_LIST_PRESETS } from "./users-list";
import { oidcLoginSection } from "./views/oidc-section";

export {
  offersAccountAction,
  useIssuePasswordAction,
  useRenameAction,
  useResetPasswordAction,
  useSetActiveActions,
  type UserAccountAction,
} from "./account-actions";
export {
  IamLoginPage,
  IAM_LOGIN_BACKGROUND_IMAGE_URLS,
  type IamLoginPageProps,
} from "./IamLoginPage";
export { userDisplayName, type UserDisplayNameInput } from "./identity-labels";
export { ShareAccessCompact, ShareAccessDialog, ShareAccessRailGroup, useAccessRole, useAccessVisibility,
  type AccessRoleOwnerProps, type AccessRoleState, type ShareAccessDialogProps } from "./ShareAccess";
export {
  assignmentSubjectOptions,
  useAssignmentSubjects,
  type AssignmentSubjectOption,
  type AssignmentSubjectsResult,
  type UseAssignmentSubjectsOptions,
} from "./assignment-subjects";
export {
  PrincipalAccessTab,
  usePrincipalAccessRecordTab,
} from "./PrincipalAccess";
// The managed people list mounts lazily from `@angee/iam/users` (UsersPage); its columns and presets:
export { USER_LIST_COLUMNS, USER_LIST_PRESETS, USER_LIST_PRESET_IDS, USER_MODEL } from "./users-list";

// IAM is a first-class app-rail destination, including the inbound OIDC sign-in
// provider admin; a route-less parent inherits its first child's target.
const identityMenu: readonly BaseMenuItem[] = [
  {
    // Route-less app root: the rail icon inherits its target from the first
    // child (Overview), so `iam.overview` is referenced by exactly one menu item.
    id: "iam",
    label: "IAM",
    icon: "auth",
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
  widgets: {
    assignmentSubjects: subjectsWidget({ kinds: ["user", "group"] }),
    assignmentUsers: subjectsWidget({ kinds: ["user"] }),
  },
  routes: [
    {
      name: "iam.login.callback",
      path: LOGIN_CALLBACK_PATH,
      layout: "public",
      component: lazyRouteComponent(() => import("./OAuthCallbackPage"), "OAuthCallbackPage"),
    },
    { name: "iam.overview", path: "/iam", component: lazyRouteComponent(() => import("./views/OverviewPage"), "OverviewPage") },
    ...resourcePageRoutes("iam.users", "/iam/users", lazyRouteComponent(() => import("./views/UsersPage"), "UsersPage"), "iam.User"),
    { name: "iam.roles", path: "/iam/roles", resource: "iam.Role", component: lazyRouteComponent(() => import("./views/RolesPage"), "RolesPage") },
    ...resourcePageRoutes("iam.groups", "/iam/groups", lazyRouteComponent(() => import("./views/GroupsPage"), "GroupsPage"), "iam.Group"),
    { name: "iam.grants", path: "/iam/grants", resource: "iam.Grant", component: lazyRouteComponent(() => import("./views/GrantsPage"), "GrantsPage") },
    { name: "iam.relationships", path: "/iam/relationships", resource: "iam.Relationship", component: lazyRouteComponent(() => import("./views/RelationshipsPage"), "RelationshipsPage") },
    { name: "iam.schema", path: "/iam/schema", component: lazyRouteComponent(() => import("./views/SchemaPage"), "SchemaPage") },
  ],
  resourceViews: USER_LIST_PRESETS,
  menus: identityMenu,
  i18n: { iam: enIamMessages },
  containers: {
    // A model's access owners register their roles and visibility for the Share panel.
    "iam#access-roles": { models: true },
    "iam#access-visibility": { models: true },
    "auth.login#method": {
      "iam.oauth-login": { content: createElement(OAuthLoginMethods) },
    },
    // Account verbs on every saved user form, each offered by the row's `account_actions`.
    "iam.User#actions-menu": {
      "iam.set-active": {
        sequence: 5,
        requiredFields: [...ACCOUNT_ACTION_FIELDS, "is_active"],
        content: createElement(SetActiveRecordActions),
      },
      "iam.rename": {
        sequence: 6,
        requiredFields: [...ACCOUNT_ACTION_FIELDS, "first_name", "last_name"],
        content: createElement(RenameRecordAction),
      },
      "iam.issue-password": {
        sequence: 7,
        requiredFields: ACCOUNT_ACTION_FIELDS,
        content: createElement(IssuePasswordRecordAction),
      },
      "iam.reset-password": {
        sequence: 8,
        requiredFields: ACCOUNT_ACTION_FIELDS,
        content: createElement(ResetPasswordRecordAction),
      },
    },
    // The users list's columns; other addons add seat columns by sequence.
    "iam.users#columns": USER_LIST_COLUMNS,
    "resource#utilities": {
      "iam.share-list": { sequence: 20, content: createElement(ShareListChrome) },
    },
    "form#chrome": {
      "iam.share-record": { sequence: 20, content: createElement(ShareRecordChrome) },
    },
    "integrate.OAuthClient#sections": {
      "iam.oidc-login": {
        sequence: 10,
        // OIDC login lives on the OAuth client itself; this contributes the OIDC tab
        // into integrate's OAuth-client form, gated to the OIDC provider types this
        // addon owns. No separate OIDC page/model — it's the same OAuthClient row.
        content: oidcLoginSection,
      },
    },
  },
});

export default iam;
