import { defineBaseAddon, resourcePageRoutes, type MenuDeclarations } from "@angee/app";
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
import { subjectsWidget } from "./assignment-subject-widget";
import { USER_LIST_COLUMNS, USER_LIST_PRESETS } from "./users-list";

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
export { USER_SUBJECT_PREFIX, subjectUserId, userSubject } from "./subjects";
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

// IAM is a first-class app-rail destination; a route-less parent inherits its
// first child's target.
const identityMenu: MenuDeclarations = {
  "iam": { label: "IAM", icon: "auth" },
  "iam.overview": { label: "Overview", route: "iam.overview", icon: "home", parent: "iam" },
  "iam.users.group": { label: "Users", icon: "users", parent: "iam" },
  "iam.users": { label: "Users", route: "iam.users", icon: "users", requires: "iam.User#list", parent: "iam.users.group" },
  "iam.groups": { label: "Groups", route: "iam.groups", icon: "users", parent: "iam.users.group" },
  "iam.roles.group": { label: "Roles", icon: "auth", parent: "iam" },
  "iam.roles": { label: "Roles", route: "iam.roles", icon: "auth", parent: "iam.roles.group" },
  "iam.grants": { label: "Grants", route: "iam.grants", icon: "check", parent: "iam.roles.group" },
  "iam.relationships": { label: "Relationships", route: "iam.relationships", icon: "share", parent: "iam.roles.group" },
  "iam.schema": { label: "Schema", route: "iam.schema", icon: "columns", parent: "iam.roles.group" },
};

const iam = defineBaseAddon({
  id: "iam",
  widgets: {
    assignmentSubjects: subjectsWidget({ kinds: ["user", "group"] }),
    assignmentUsers: subjectsWidget({ kinds: ["user"] }),
  },
  routes: [
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
  },
});

export default iam;
