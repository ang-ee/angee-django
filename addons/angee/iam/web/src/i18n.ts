// English fallback strings for the IAM addon's user-facing copy. The host
// runtime owns the active translations; these are the defaults used when a key
// is missing. Components resolve them through `useIamT()` (below), and the
// manifest contributes this bundle as the `iam` namespace.

import { createNamespaceT } from "@angee/ui";

export const enIamMessages: Record<string, string> = {
  // Shared action labels.
  "revoke": "Revoke",

  // Typed assignment subjects shared by workflow and approval controls.
  "assignmentSubjects.users": "Users",
  "assignmentSubjects.groups": "Groups",
  "assignmentSubjects.label": "Reviewers",
  "assignmentSubjects.loading": "Loading reviewers…",
  "assignmentSubjects.add": "Add reviewer",
  "assignmentSubjects.unavailable": "Reviewer choices could not be loaded.",

  // Users page — form-section labels and actions.
  "users.group.profile": "Profile",
  "users.group.access": "Access",
  "users.resetPassword": "Reset password",
  "users.resetPassword.title": "Reset password",
  "users.resetPassword.body": "Set a new password for this user.",
  "users.resetPassword.fieldLabel": "New password",
  "users.deactivate": "Deactivate",
  "users.activate": "Activate",

  // OAuth login methods (the public sign-in slot).
  "login.loadingOptions": "Loading sign-in options…",
  "login.providersUnavailable": "Sign-in providers unavailable",
  "login.passwordStillAvailable":
    "Username and password sign-in is still available.",
  "login.continueWith": "Continue with {provider}",
  "login.startFailed": "Sign-in could not start",
  "login.startError": "Could not start sign-in.",

  // OAuth callback page.
  "callback.completing": "Completing sign-in…",
  "callback.confirming": "Your session is being confirmed.",
  "callback.signInFailed": "Could not sign in",
  "callback.backToSignIn": "Back to sign in",
  "callback.completeError": "Could not complete sign-in.",
  "callback.browserOnly":
    "The sign-in callback can only be completed in a browser.",
  "callback.missingInfo":
    "The sign-in callback is missing required information.",

  // Overview dashboard — metric band.
  "overview.metric.users": "Users",
  "overview.metric.roles": "Roles",
  "overview.metric.grants": "Grants",
  "overview.metric.relationships": "Relationships",
  "overview.metric.privileged": "Privileged",
  "overview.metric.privilegedDetail": "admin-tier grants",
  "overview.metric.unassigned": "No direct role",
  "overview.metric.unassignedDetail": "no direct roles",

  // Overview dashboard — grant composer.
  "overview.grant.title": "Grant access",
  "overview.grant.summary": "Direct role binding for a user or group.",
  "overview.grant.subject": "Recipient",
  "overview.grant.role": "Role",
  "overview.grant.selectRole": "Select role",
  "overview.grant.submit": "Grant",
  "overview.grant.failedTitle": "Role was not granted",
  "overview.grant.error": "Could not grant role.",

  // Overview dashboard — peek panels.
  "overview.privileged.title": "Privileged grants",
  "overview.privileged.summary": "{count} admin-tier grants",
  "overview.privileged.empty": "No admin-tier grants.",
  "overview.namespaces.title": "Role namespaces",
  "overview.namespaces.summary": "{count} namespaces",
  "overview.namespaces.roleCount_one": "{count} role",
  "overview.namespaces.roleCount_other": "{count} roles",
  "overview.namespaces.grantCount": "{count} grants",
  "overview.namespaces.empty": "No roles defined.",
  "overview.unassigned.title": "Users without direct roles",
  "overview.unassigned.summary": "{count} users; access through groups and record grants may still apply.",
  "overview.unassigned.empty": "Every user has a direct role.",

  // OIDC sign-in providers page — form-section labels and the discover action.

  // Grants page.
  "grants.column.subject": "Recipient",
  "grants.column.role": "Role",
  "grants.column.namespace": "Namespace",
  "grants.revoke.title": "Revoke role?",
  "grants.revoke.body": "Revoke {role} from {subject}?",
  "grants.revoke.cancel": "Keep role",
  "grants.revoke.error": "Could not revoke role.",
  "grants.revoke.failedTitle": "Role was not revoked",

  "subject.type": "Recipient type",
  "subject.user": "User or service",
  "subject.group": "Group",
  "group.members": "Members",
  "group.bindings": "Bindings",
  "group.member": "Member",
  "group.subject": "Subject",
  "group.resource": "Resource",
  "group.relation": "Relation",
  "group.caveat": "Condition",
  "group.add": "Add member",
  "group.addError": "Could not add member.",
  "group.membersDescription": "People and service users inherit the group's bindings.",
  "group.remove": "Remove member",
  "group.removeTitle": "Remove group member?",
  "group.removeDescription": "Remove {name} from this group? Independent grants will remain.",
  "group.removeError": "Could not remove member.",
  "group.noMembers": "This group has no members.",
  "group.noBindings": "No direct bindings. Grant this group a record relation through Share or a role through the Permissions Hub.",
  "roles.declaration": "Declaration",
  "roles.declared": "Schema",
  "roles.derived": "Derived (read-only)",
  "roles.legacy": "Legacy grant",

  // Relationships page.
  "relationships.column.resourceRef": "Resource Ref",
  "relationships.column.subjectRef": "Subject Ref",
  "relationships.column.resourceType": "Resource Type",
  "relationships.column.resourceId": "Resource ID",
  "relationships.column.relation": "Relation",
  "relationships.column.subjectType": "Subject Type",
  "relationships.column.subjectId": "Subject ID",
  "relationships.column.caveat": "Caveat",

  // Schema page.
  "schema.unavailable": "Schema unavailable",
  "schema.loading": "Loading schema…",
  "schema.searchPlaceholder": "Search schema",
  "schema.resourceTypesLabel": "Resource types",
  "schema.noMatches": "No matching resource types.",
  "schema.permissionGraph": "Permission Graph",
  "schema.nodeCount": "{count} nodes",
  "schema.inspector": "Inspector",
  "schema.noneSelected": "No resource type selected.",
  "schema.relations": "Relations",
  "schema.permissions": "Permissions",
  "schema.noSubjects": "No subjects",
  "schema.noConditions": "No conditions",
  "schema.noRelations": "No relations.",
  "schema.noPermissions": "No permissions.",
  "schema.resourceDetail": "{relations} relations / {permissions} permissions",
  "schema.subjectCount_one": "{count} subject",
  "schema.subjectCount_other": "{count} subjects",
  "schema.conditionCount_one": "{count} condition",
  "schema.conditionCount_other": "{count} conditions",
  "schema.edge.contains": "contains",
};

// A translator bound to the `iam` namespace: resolves against the host runtime's
// merged i18n first, then falls back to the bundled English. Thin alias over the
// shared `createNamespaceT` owner, so the copy still renders provider-less.
export const useIamT = createNamespaceT("iam", enIamMessages);
