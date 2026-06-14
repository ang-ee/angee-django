// English fallback strings for the IAM addon's user-facing copy. The host
// runtime owns the active translations; these are the defaults used when a key
// is missing. Components resolve them through `useIamT()` (below), and the
// manifest contributes this bundle as the `iam` namespace.

import { useNamespaceT, type MessageVars } from "@angee/sdk";

export const enIamMessages: Record<string, string> = {
  // Shared action labels.
  "iam.revoke": "Revoke",

  // Users page — form-section labels and actions.
  "iam.users.group.profile": "Profile",
  "iam.users.group.access": "Access",
  "iam.users.resetPassword": "Reset password",
  "iam.users.resetPassword.title": "Reset password",
  "iam.users.resetPassword.body": "Set a new password for this user.",
  "iam.users.resetPassword.fieldLabel": "New password",
  "iam.users.deactivate": "Deactivate",
  "iam.users.activate": "Activate",

  // External accounts page — form-section labels and revoke confirm.
  "iam.externalAccounts.group.identity": "Identity",
  "iam.externalAccounts.provider": "Provider",
  "iam.externalAccounts.revoke.title": "Revoke this account?",
  "iam.externalAccounts.revoke.body":
    "The linked identity can no longer be used to sign in.",

  // OAuth login methods (the public sign-in slot).
  "iam.login.loadingOptions": "Loading sign-in options...",
  "iam.login.providersUnavailable": "Sign-in providers unavailable",
  "iam.login.passwordStillAvailable":
    "Username and password sign-in is still available.",
  "iam.login.continueWith": "Continue with {provider}",
  "iam.login.startFailed": "Sign-in could not start",
  "iam.login.startError": "Could not start sign-in.",

  // OAuth callback page.
  "iam.callback.completing": "Completing sign-in...",
  "iam.callback.confirming": "Your session is being confirmed.",
  "iam.callback.signInFailed": "Could not sign in",
  "iam.callback.backToSignIn": "Back to sign in",
  "iam.callback.completeError": "Could not complete sign-in.",
  "iam.callback.browserOnly":
    "The sign-in callback can only be completed in a browser.",
  "iam.callback.missingInfo":
    "The sign-in callback is missing required information.",

  // Overview dashboard — metric band.
  "iam.overview.metric.users": "Users",
  "iam.overview.metric.roles": "Roles",
  "iam.overview.metric.grants": "Grants",
  "iam.overview.metric.relationships": "Relationships",
  "iam.overview.metric.privileged": "Privileged",
  "iam.overview.metric.privilegedDetail": "admin-tier grants",
  "iam.overview.metric.unassigned": "Unassigned",
  "iam.overview.metric.unassignedDetail": "no direct roles",

  // Overview dashboard — grant composer.
  "iam.overview.grant.title": "Grant access",
  "iam.overview.grant.summary": "Direct role binding for a user or group.",
  "iam.overview.grant.principal": "Principal",
  "iam.overview.grant.role": "Role",
  "iam.overview.grant.loadingUsers": "Loading users",
  "iam.overview.grant.selectUser": "Select user",
  "iam.overview.grant.selectRole": "Select role",
  "iam.overview.grant.truncated": "Showing first {shown} of {total} users.",
  "iam.overview.grant.submit": "Grant",
  "iam.overview.grant.failedTitle": "Role was not granted",
  "iam.overview.grant.chooseBoth":
    "Choose a principal and role before granting access.",
  "iam.overview.grant.error": "Could not grant role.",

  // Overview dashboard — peek panels.
  "iam.overview.privileged.title": "Privileged grants",
  "iam.overview.privileged.summary": "{count} admin-tier grants",
  "iam.overview.privileged.empty": "No admin-tier grants.",
  "iam.overview.namespaces.title": "Role namespaces",
  "iam.overview.namespaces.summary": "{count} namespaces",
  "iam.overview.namespaces.roleCount.one": "{count} role",
  "iam.overview.namespaces.roleCount.other": "{count} roles",
  "iam.overview.namespaces.grantCount": "{count} grants",
  "iam.overview.namespaces.empty": "No roles defined.",
  "iam.overview.unassigned.title": "Unassigned principals",
  "iam.overview.unassigned.summary": "{count} without direct roles",
  "iam.overview.unassigned.empty": "Every principal has a role.",

  // Providers page — form-section labels and actions.
  "iam.providers.group.client": "Client",
  "iam.providers.group.endpoints": "Endpoints",
  "iam.providers.group.loginPolicy": "Login policy",
  "iam.providers.group.scopes": "Scopes",
  "iam.providers.group.oauthMetadata": "OAuth metadata",
  "iam.providers.action.connect": "Connect account",
  "iam.providers.action.discover": "Discover endpoints",
  "iam.providers.action.disable": "Disable",
  "iam.providers.action.enable": "Enable",
  "iam.providers.connect.startError": "Could not start account connection.",
  "iam.providers.connect.redirecting": "Redirecting...",
  "iam.providers.connect.openAuthorize": "Open the authorization page",
  "iam.providers.connect.instructions": ", approve, then paste the code it shows below.",
  "iam.providers.connect.codeLabel": "Authorization code",
  "iam.providers.connect.codePlaceholder": "code#state",
  "iam.providers.connect.codeIncomplete":
    "That code looks incomplete — paste the full value the page showed.",
  "iam.providers.connect.codeMismatch":
    "That code is from a different attempt — start the connection again.",
  "iam.providers.connect.stateIncomplete":
    "Connection state is incomplete — start the connection again.",
  "iam.providers.connect.connected": "Account connected.",

  // Credentials page — form-section labels and actions.
  "iam.credentials.group.health": "Health",
  "iam.credentials.action.reveal": "Reveal secret",
  "iam.credentials.reveal.noSecret": "This credential has no stored secret to reveal.",
  "iam.credentials.reveal.title": "Credential secret",
  "iam.credentials.reveal.body":
    "Copy it now — it is shown on request only and never kept in the form.",
  "iam.credentials.reveal.secretLabel": "Secret",
  "iam.credentials.revoke.title": "Revoke this credential?",
  "iam.credentials.revoke.body": "Anything using it to authenticate will stop working.",

  // Account-connect callback page.
  "iam.connectCallback.completing": "Connecting account...",
  "iam.connectCallback.confirming": "Your account connection is being confirmed.",
  "iam.connectCallback.failedTitle": "Could not connect account",
  "iam.connectCallback.backToProviders": "Back to providers",
  "iam.connectCallback.browserOnly":
    "The account callback can only be completed in a browser.",
  "iam.connectCallback.missingInfo":
    "The account callback is missing required information.",
  "iam.connectCallback.completeError": "Could not connect account.",

  // Grants page.
  "iam.grants.group.namespace": "Namespace",
  "iam.grants.column.principal": "Principal",
  "iam.grants.column.role": "Role",
  "iam.grants.column.namespace": "Namespace",
  "iam.grants.revoke.title": "Revoke role?",
  "iam.grants.revoke.body": "Revoke {role} from {principal}?",
  "iam.grants.revoke.cancel": "Keep role",
  "iam.grants.revoke.error": "Could not revoke role.",
  "iam.grants.revoke.failedTitle": "Role was not revoked",

  // Relationships page.
  "iam.relationships.group.resourceType": "Resource Type",
  "iam.relationships.group.subjectType": "Subject Type",
  "iam.relationships.group.relation": "Relation",
  "iam.relationships.column.resourceRef": "Resource Ref",
  "iam.relationships.column.subjectRef": "Subject Ref",
  "iam.relationships.column.resourceType": "Resource Type",
  "iam.relationships.column.resourceId": "Resource ID",
  "iam.relationships.column.relation": "Relation",
  "iam.relationships.column.subjectType": "Subject Type",
  "iam.relationships.column.subjectId": "Subject ID",
  "iam.relationships.column.caveat": "Caveat",

  // Schema page.
  "iam.schema.unavailable": "Schema unavailable",
  "iam.schema.loading": "Loading schema...",
  "iam.schema.searchPlaceholder": "Search schema",
  "iam.schema.resourceTypesLabel": "Resource types",
  "iam.schema.noMatches": "No matching resource types.",
  "iam.schema.permissionGraph": "Permission Graph",
  "iam.schema.nodeCount": "{count} nodes",
  "iam.schema.noneSelected": "No resource type selected.",
  "iam.schema.relations": "Relations",
  "iam.schema.permissions": "Permissions",
  "iam.schema.noSubjects": "No subjects",
  "iam.schema.noConditions": "No conditions",
  "iam.schema.noRelations": "No relations.",
  "iam.schema.noPermissions": "No permissions.",
  "iam.schema.resourceDetail": "{relations} relations / {permissions} permissions",
  "iam.schema.subjectCount.one": "{count} subject",
  "iam.schema.subjectCount.other": "{count} subjects",
  "iam.schema.conditionCount.one": "{count} condition",
  "iam.schema.conditionCount.other": "{count} conditions",
  "iam.schema.edge.contains": "contains",
};

// A translator bound to the `iam` namespace: resolves against the host runtime's
// merged i18n first, then falls back to the bundled English. Thin alias over the
// shared `useNamespaceT` owner, so the copy still renders provider-less.
export function useIamT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("iam", enIamMessages);
}
